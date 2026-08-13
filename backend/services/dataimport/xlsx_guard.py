"""Decompression-bomb guard for every ``openpyxl.load_workbook()`` call in
this app (adversarial finding, 2026-08-12/13).

**The finding.** ``openpyxl.load_workbook(..., read_only=True)`` streams
*rows* lazily, but it does NOT bound the shared-strings table:
``openpyxl.reader.excel.ExcelReader.read_strings`` runs unconditionally
(``read_only`` or not) and ``openpyxl.reader.strings.read_string_table``
fully materialises every ``<si>`` text via ``iterparse`` into one Python
list before ``load_workbook`` even returns. A small, highly-compressible
``xl/sharedStrings.xml`` (or ``xl/styles.xml``) member can therefore make
openpyxl allocate an arbitrary amount of memory from a tiny upload, and the
various request-body byte caps in this app (``routers.datasets
.MAX_UPLOAD_BYTES`` = 250 MB, ``genesis.side_inputs.MAX_SIDE_INPUT_BYTES`` =
32 MB) only bound the COMPRESSED bytes received over the wire.

**Measured** (this unit, 2026-08-13 -- every number below is a real run, not
an estimate; see the scratch scripts this session used to produce them):

* A crafted bomb -- one legitimate, tiny ``.xlsx`` (2 cells) with
  ``xl/sharedStrings.xml`` replaced by a single ``<si><t>`` run of 'A'
  declaring 524,288,000 bytes uncompressed -- is 514,742 bytes on disk
  (ratio 1,028:1 for that member). ``openpyxl.load_workbook(path,
  read_only=True, data_only=True)`` -- the exact call every consumer below
  makes -- raised this process's working set by 551,841,792 bytes (peak
  579,244,032) in 1.23 s to open a 502 KB file. Smaller runs confirmed the
  relationship holds linearly (50 MB declared -> +78 MB working set; 300 MB
  declared -> +341 MB, peak 356 MB matching a directly-measured
  314,572,603-character parsed string).
* **Do zipfile/openpyxl trust the declared size, or discover the truth by
  reading?** Tested directly: a member whose CENTRAL DIRECTORY entry (what
  ``zipfile.ZipFile.infolist()``/``getinfo()`` report, and what any
  size-based precheck reads) declares a SMALL size while the raw deflate
  stream actually holds much more real data past that point --
  ``ZipFile.open()``/``.read()`` (which is what ``ExcelReader.read_strings``
  and every ``iter_rows`` call use) stop producing bytes the INSTANT they
  have emitted exactly the central directory's declared ``file_size`` --
  never more, regardless of how much more the compressed stream could give.
  If the recorded CRC does not match that truncated prefix (i.e. the header
  really is internally inconsistent) it raises ``BadZipFile: Bad CRC-32``
  before a caller ever sees a byte. So a precheck built on
  ``ZipInfo.file_size`` is not merely a heuristic here -- it is sound: real
  decompression cannot exceed what the header (the same header the precheck
  reads) declares.
  **Verified citation** (CPython 3.11.2, ``Lib/zipfile.py``, the exact
  interpreter this venv runs -- see ``.venv``): ``ZipExtFile.__init__`` sets
  ``self._left = zipinfo.file_size`` (line 824), and ``ZipExtFile._read1``
  truncates every decompressed chunk to that remaining count --
  ``data = data[:self._left]`` (lines 1037-1039). This is one version's
  implementation, not a documented language guarantee -- it can change --
  which is exactly why this module does NOT bet the guard on it being
  forever true; see "two layers, one of them insurance" below.
* **The legitimate ceiling**, measured across every real workbook this
  project has (``client-data/유성구-교통량-워크북/`` -- 11 Daejeon count
  exports --, ``client-data/Traffic signal data.zip`` -- 3 WA SCATS
  exports --, and ``client-data/Calc_MurdochDr_BaseTurningMov_JH_edit_
  20260526.xlsx``, the base validation workbook, 168 members): largest
  single member 17,915,416 bytes decompressed (Daejeon 도안중네거리's
  ``sheet1.xml``); largest TOTAL across one workbook's members 22,948,784
  bytes (the Murdoch base workbook, 168 members); worst per-member
  compression ratio among members over 4 KB declared, 12.43:1 (the Murdoch
  workbook's own ``xl/styles.xml``, 60,304 declared / 4,851 compressed --
  worksheet XML typically ran 9-11:1). A bomb's member ratio was 1,028:1 --
  two orders of magnitude past the worst real ratio measured.

**Two layers, one of them insurance, not two independent protections:**

1. **Header precheck** -- member count, each member's declared
   ``file_size``, the running total, and each member's declared
   compression ratio -- read from ``ZipInfo`` with ZERO decompression.
   Rejects an actual bomb (any of the sizes above) instantly, before a
   single byte is inflated, which is also why this layer runs first: it is
   what keeps the guard itself cheap to run against a hostile upload. This
   is the layer doing the real work.
2. **Streamed re-measurement** -- once (and only once) the header precheck
   clears, every member is decompressed for real via ``archive.open()``,
   counting produced bytes and discarding them, with the SAME per-member and
   total caps enforced as hard stops mid-stream. Being honest about what
   this buys on THIS zip implementation: per the verified citation above,
   ``ZipExtFile`` can never emit more than ``zipinfo.file_size`` bytes for a
   member -- the exact number layer 1 already compared against the same
   caps. So if layer 1 passed a member (its declared size already <= the
   cap), layer 2 streaming that same member cannot mathematically exceed
   the cap either -- its own ``_streamed_member_over_cap_error``/
   ``_streamed_total_over_cap_error`` are provably unreachable on this
   interpreter, not a second independent gate. What layer 2 is REALLY for:
   forward insurance against a different zip implementation (a future
   CPython, PyPy, an alternate library some call site switches to) that
   does not preserve this truncation behaviour -- if that guarantee ever
   stops holding, layer 2 is what catches it, rather than the guard quietly
   depending on an implementation detail forever. Cheap in practice
   regardless, because every legitimate workbook measured is <23 MB total,
   sub-second to stream in full.

Placement: ``services/dataimport/`` rather than a new top-level module,
because two of the four call sites (``adapters/daejeon.py``,
``adapters/scats_wa.py``) already live inside this package and import
sibling modules with ``from ..xxx import ...``; the other two
(``services/validation.py``, ``services/workbook_mapping.py``) sit outside
it but already import FROM other ``services.*`` modules routinely, and nothing
in this package imports either of them (checked: no
``services.validation``/``services.workbook_mapping`` reference anywhere
under ``services/dataimport/``), so `services/validation.py` and
`services/workbook_mapping.py` importing this module the other direction
creates no cycle. This module imports nothing else from the package (no
``schema.py``, no ``base.py``) so it carries no risk of becoming one itself.

Nothing here imports Vissim.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any


class XlsxBombError(Exception):
    """A workbook whose zip structure declares -- or, independently,
    actually streams -- more decompressed data than any legitimate
    workbook this project has measured. Raised BEFORE openpyxl (or anything
    else) is given the file, carrying the measured numbers in both the
    message (for a caller that just does ``str(exc)``) and ``detail`` (for a
    caller building a structured refusal).

    Every call site is expected to catch this and translate it into its own
    existing refusal vocabulary -- see the module docstring's placement
    note and each adapter's/service's own call site.
    """

    def __init__(self, message: str, *, detail: dict[str, Any]):
        super().__init__(message)
        self.detail = detail


# ---------------------------------------------------------------------------
# Constants. Every number here is cited from the measurements in the module
# docstring, not chosen by feel. Re-derive them with
# services/dataimport (or ask BUILDER for the scratch scripts) if the real
# workbook corpus grows meaningfully past what is measured above.
# ---------------------------------------------------------------------------

#: Largest single-member decompressed size any real workbook measured for
#: this guard needed: 17,915,416 bytes (Daejeon 도안중네거리's sheet1.xml).
#: Cap set to ~5.9x that.
MAX_MEMBER_DECOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB

#: Largest TOTAL decompressed size across one real workbook's members:
#: 22,948,784 bytes (the 168-member Murdoch base workbook). Cap set to
#: ~13.7x that.
MAX_TOTAL_DECOMPRESSED_BYTES = 300 * 1024 * 1024  # 300 MB

#: Worst per-member compression ratio measured among real members over
#: ``_RATIO_CHECK_FLOOR_BYTES`` declared: 12.43:1 (same Murdoch workbook's
#: xl/styles.xml). A single-character-run bomb measures 1,028:1. Cap set to
#: ~4.8x the worst real ratio -- and over two orders of magnitude below a
#: real bomb's ratio, so there is no plausible legitimate workbook this
#: would catch by accident.
MAX_COMPRESSION_RATIO = 60.0

#: Below this declared size, DEFLATE's fixed per-member overhead alone can
#: produce a large-looking ratio on completely unremarkable content (e.g. a
#: 200-byte docProps/app.xml at 149 bytes compressed is a real, harmless
#: 1.3:1 -- but a handful of highly-repetitive bytes under this floor could
#: swing past MAX_COMPRESSION_RATIO on noise alone). Matches the floor used
#: to derive MAX_COMPRESSION_RATIO from the real corpus.
_RATIO_CHECK_FLOOR_BYTES = 4096

#: Largest member count any real workbook measured: 168 (the Murdoch base
#: workbook). Cap set to ~11.9x that -- guards the classic "many tiny
#: members" (42.zip-style) bomb shape, which the size/ratio caps above do
#: not by themselves bound.
MAX_MEMBERS = 2000

#: Streaming granularity for the re-measurement pass -- matches
#: services/datasets.py's ``_MEMBER_CHUNK_BYTES`` convention (big enough
#: that a real workbook streams in a handful of reads, small enough that
#: overshoot past a cap before this notices is bounded by one chunk).
_STREAM_CHUNK_BYTES = 1024 * 1024


def _member_over_cap_error(label: str, filename: str, declared: int, compressed: int) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: {filename!r} declares {declared:,} bytes decompressed "
        f"(cap {MAX_MEMBER_DECOMPRESSED_BYTES:,}; the largest real workbook member measured "
        f"for this guard is 17,915,416 bytes).",
        detail={
            "code": "xlsx_member_too_large",
            "member": filename,
            "declared_bytes": declared,
            "compressed_bytes": compressed,
            "cap_bytes": MAX_MEMBER_DECOMPRESSED_BYTES,
        },
    )


def _ratio_over_cap_error(label: str, filename: str, declared: int, compressed: int, ratio: float) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: {filename!r} compresses {declared:,} declared bytes from only {compressed:,} bytes "
        f"on disk (ratio {ratio:,.0f}:1; cap {MAX_COMPRESSION_RATIO:.0f}:1 -- the worst ratio measured "
        f"across every real workbook for this guard is 12.43:1).",
        detail={
            "code": "xlsx_compression_ratio_too_high",
            "member": filename,
            "declared_bytes": declared,
            "compressed_bytes": compressed,
            "ratio": ratio,
            "cap_ratio": MAX_COMPRESSION_RATIO,
        },
    )


def _total_over_cap_error(label: str, total: int, member_count: int) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: {total:,} bytes declared decompressed across {member_count} zip entries "
        f"(cap {MAX_TOTAL_DECOMPRESSED_BYTES:,}; the largest real workbook measured for this "
        f"guard totals 22,948,784 bytes across 168 entries).",
        detail={
            "code": "xlsx_total_too_large",
            "total_declared_bytes": total,
            "member_count": member_count,
            "cap_bytes": MAX_TOTAL_DECOMPRESSED_BYTES,
        },
    )


def _too_many_members_error(label: str, member_count: int) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: {member_count} zip entries (cap {MAX_MEMBERS}; the largest real workbook "
        f"measured for this guard has 168).",
        detail={"code": "xlsx_too_many_members", "member_count": member_count, "cap": MAX_MEMBERS},
    )


def _streamed_member_over_cap_error(label: str, filename: str, produced: int) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: {filename!r} decompressed past {produced:,} bytes while streaming "
        f"(cap {MAX_MEMBER_DECOMPRESSED_BYTES:,}). The declared zip header size could not be "
        f"corroborated by actually reading the member.",
        detail={
            "code": "xlsx_member_streamed_too_large",
            "member": filename,
            "streamed_bytes": produced,
            "cap_bytes": MAX_MEMBER_DECOMPRESSED_BYTES,
        },
    )


def _streamed_total_over_cap_error(label: str, total: int) -> XlsxBombError:
    return XlsxBombError(
        f"{label}: decompressed past {total:,} bytes total while streaming "
        f"(cap {MAX_TOTAL_DECOMPRESSED_BYTES:,}).",
        detail={
            "code": "xlsx_total_streamed_too_large",
            "streamed_bytes": total,
            "cap_bytes": MAX_TOTAL_DECOMPRESSED_BYTES,
        },
    )


def _inspect(archive: zipfile.ZipFile, *, label: str) -> None:
    """Both layers described in the module docstring, in order. Returns
    normally (no exception) for anything that clears every cap, INCLUDING a
    corrupt/non-xlsx zip -- diagnosing "is this even readable" is the real
    reader's job (``openpyxl``, via each adapter's own ``AdapterError``
    path), not this guard's; it only judges size.
    """
    try:
        infos = archive.infolist()
    except Exception:
        return  # not a size problem -- let the real reader describe it

    if len(infos) > MAX_MEMBERS:
        raise _too_many_members_error(label, len(infos))

    total_declared = 0
    for info in infos:
        declared = max(int(info.file_size or 0), 0)
        compressed = max(int(info.compress_size or 0), 0)
        total_declared += declared

        if declared > MAX_MEMBER_DECOMPRESSED_BYTES:
            raise _member_over_cap_error(label, info.filename, declared, compressed)

        if declared > _RATIO_CHECK_FLOOR_BYTES:
            ratio = declared / max(compressed, 1)
            if ratio > MAX_COMPRESSION_RATIO:
                raise _ratio_over_cap_error(label, info.filename, declared, compressed, ratio)

    if total_declared > MAX_TOTAL_DECOMPRESSED_BYTES:
        raise _total_over_cap_error(label, total_declared, len(infos))

    # Layer 2: independently re-measure by actually streaming every member
    # and discarding the bytes, never trusting `declared`/`compressed` above
    # for anything but the fast-fail already done. See module docstring.
    total_streamed = 0
    for info in infos:
        produced = 0
        try:
            with archive.open(info) as stream:
                while True:
                    chunk = stream.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    produced += len(chunk)
                    total_streamed += len(chunk)
                    if produced > MAX_MEMBER_DECOMPRESSED_BYTES:
                        raise _streamed_member_over_cap_error(label, info.filename, produced)
                    if total_streamed > MAX_TOTAL_DECOMPRESSED_BYTES:
                        raise _streamed_total_over_cap_error(label, total_streamed)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            # A member this guard cannot even OPEN, for reasons that have
            # nothing to do with size: BadZipFile (corrupt/inconsistent,
            # e.g. the CRC mismatch a lying declared size produces --
            # see module docstring), RuntimeError (``ZipFile.open()`` raises
            # exactly this for a member with the encryption general-purpose
            # flag bit set and no password -- CPython 3.11.2 zipfile.py
            # lines 1591-1599 -- a REAL regression this guard caused: such a
            # file opens with ZERO exceptions under plain
            # ``openpyxl.load_workbook(read_only=True)``, which never
            # touches that member, so a bare ``except zipfile.BadZipFile``
            # here let the raw RuntimeError escape past this guard into
            # every adapter's narrower ``except`` clauses -- caught this
            # unit's adversarial review, fixed here), and NotImplementedError
            # (an unsupported flag bit -- e.g. flag bit 5 "compressed patched
            # data" or bit 6 "strong encryption", same source, lines
            # 1571-1577). None of these are a size problem -- not this
            # guard's question to answer; the real reader (openpyxl, via
            # each adapter's own AdapterError path) already has a way to
            # describe (or simply ignore) an unopenable member. Deliberately
            # NOT a bare ``except Exception`` -- a guard that swallows its
            # own bugs is worse than one that has them.
            return


def guard_xlsx_path(path: str | Path, *, label: str | None = None) -> None:
    """Call BEFORE ``openpyxl.load_workbook(path, ...)`` touches the same
    path. Raises :class:`XlsxBombError`; returns ``None`` for anything else
    (including a file that is not a valid zip at all -- that is the real
    reader's diagnosis to make, not this guard's)."""
    path = Path(path)
    effective_label = label or path.name
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return
    try:
        _inspect(archive, label=effective_label)
    finally:
        archive.close()


def guard_xlsx_bytes(content: bytes, *, label: str = "upload") -> None:
    """Call BEFORE ``openpyxl.load_workbook(BytesIO(content), ...)`` touches
    the same bytes. Operates on an independent ``BytesIO`` wrapper so the
    caller's own buffer/position is never disturbed. Same contract as
    :func:`guard_xlsx_path`."""
    from io import BytesIO

    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except (zipfile.BadZipFile, OSError):
        return
    try:
        _inspect(archive, label=label)
    finally:
        archive.close()
