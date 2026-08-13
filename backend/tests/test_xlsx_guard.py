"""Tests for the shared decompression-bomb guard (services/dataimport/
xlsx_guard.py), used by every openpyxl.load_workbook() call in this app.

Adversarial finding (2026-08-12): openpyxl.load_workbook(read_only=True)
streams sheet rows but always fully materialises xl/sharedStrings.xml (and
xl/styles.xml) into memory before returning, regardless of read_only -- see
the module's own docstring for the measured numbers (a 502 KB crafted file
raised process memory by ~552 MB). This file tests the guard IN ISOLATION:
each cap fires on its own (member size, total size, ratio, member count), a
clean small workbook passes through untouched, and a "lying header" zip is
left for the real reader rather than mis-diagnosed here. Per-consumer wiring
(daejeon/scats_wa adapters, services/validation.py, services/workbook_
mapping.py) is tested in each of those files, not duplicated here.

Every fixture is built in-process with openpyxl + zipfile/struct -- nothing
is copied from client-data/ (gitignored, proprietary, never committed).
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from io import BytesIO

import openpyxl
import pytest

from services.dataimport import xlsx_guard
from services.dataimport.xlsx_guard import XlsxBombError, guard_xlsx_bytes, guard_xlsx_path

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

_SST_HEADER = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    b'count="1" uniqueCount="1"><si><t xml:space="preserve">'
)
_SST_FOOTER = b"</t></si></sst>"

_SHARED_STRINGS_OVERRIDE = (
    b'<Override PartName="/xl/sharedStrings.xml" '
    b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
)


def _tiny_workbook_bytes() -> bytes:
    """A minimal, entirely legitimate .xlsx: two cells, no shared strings."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    ws["A2"] = "world"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _declare_shared_strings(content_types_xml: bytes) -> bytes:
    if b"sharedStrings" in content_types_xml:
        return content_types_xml
    return content_types_xml.replace(b"</Types>", _SHARED_STRINGS_OVERRIDE + b"</Types>")


def _bomb_bytes(declared_bytes: int, *, fill: bytes = b"A") -> bytes:
    """A real, valid .xlsx whose xl/sharedStrings.xml declares
    ``declared_bytes`` of a single repeated character -- openpyxl's manifest
    lookup (``read_strings()``) only finds the shared-strings part via the
    [Content_Types].xml Override, so that Override is added explicitly (a
    fresh openpyxl.Workbook() writes inline strings and carries neither the
    part nor the Override at all -- verified directly; without this the
    injected member is simply never read).
    """
    src = zipfile.ZipFile(BytesIO(_tiny_workbook_bytes()))
    sst = _SST_HEADER + fill * declared_bytes + _SST_FOOTER

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                archive.writestr(info, _declare_shared_strings(src.read(info.filename)))
            elif info.filename == "xl/sharedStrings.xml":
                archive.writestr(info, sst)
            else:
                archive.writestr(info, src.read(info.filename))
        if "xl/sharedStrings.xml" not in src.namelist():
            archive.writestr("xl/sharedStrings.xml", sst)
    return out.getvalue()


def _incompressible_bomb_bytes(declared_bytes: int) -> bytes:
    """Like :func:`_bomb_bytes`, but the filler is near-incompressible (a
    short pseudo-random block tiled out), so its RATIO stays close to 1:1 --
    isolates the SIZE cap from the RATIO cap, which a repeated-character
    filler cannot do (DEFLATE always compresses that at >1000:1).
    """
    import os

    block = os.urandom(4096)
    reps = declared_bytes // len(block) + 1
    filler = (block * reps)[:declared_bytes]
    # Base64-ish hex encoding keeps it valid XML text without needing to
    # escape arbitrary bytes; doubles the declared length, so ask for half.
    hex_filler = filler.hex().encode("ascii")[:declared_bytes]

    src = zipfile.ZipFile(BytesIO(_tiny_workbook_bytes()))
    sst = _SST_HEADER + hex_filler + _SST_FOOTER

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                archive.writestr(info, _declare_shared_strings(src.read(info.filename)))
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)
    return out.getvalue()


def _lying_header_bytes(declared_small: int, actual_large: int) -> bytes:
    """A workbook whose xl/sharedStrings.xml CENTRAL DIRECTORY entry
    declares ``declared_small`` bytes, while the real deflate stream (and
    its recorded CRC) is for ``actual_large`` bytes. ``zipfile`` truncates
    any real read to the declared size and then finds the CRC does not
    match that truncated prefix -- ``BadZipFile: Bad CRC-32`` -- rather than
    silently handing back more than declared. See
    services/dataimport/xlsx_guard.py's module docstring for the
    investigation this fixture proves.
    """
    src = zipfile.ZipFile(BytesIO(_tiny_workbook_bytes()))
    sst = _SST_HEADER + b"B" * actual_large + _SST_FOOTER
    crc_full = zlib.crc32(sst) & 0xFFFFFFFF

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                archive.writestr(info, _declare_shared_strings(src.read(info.filename)))
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)

    raw = bytearray(out.getvalue())
    target = b"xl/sharedStrings.xml"
    idx = raw.find(target)
    patched = False
    while idx != -1:
        central_start = idx - 46
        if central_start >= 0 and raw[central_start : central_start + 4] == b"PK\x01\x02":
            struct.pack_into("<I", raw, central_start + 16, crc_full)  # leave CRC as the FULL stream's
            struct.pack_into("<I", raw, central_start + 24, declared_small)  # lie: shrink declared size
            patched = True
        idx = raw.find(target, idx + 1)
    assert patched, "fixture bug: did not find the central directory record to patch"
    return bytes(raw)


# ---------------------------------------------------------------------------
# Clean pass-through
# ---------------------------------------------------------------------------


def test_a_tiny_legitimate_workbook_passes_both_path_and_bytes_entry_points(tmp_path):
    content = _tiny_workbook_bytes()
    path = tmp_path / "clean.xlsx"
    path.write_bytes(content)

    guard_xlsx_path(path)  # must not raise
    guard_xlsx_bytes(content)  # must not raise

    # And openpyxl can still actually open what the guard passed.
    wb = openpyxl.load_workbook(path, read_only=True)
    assert wb[wb.sheetnames[0]]["A1"].value == "hello"


def test_a_corrupt_non_zip_file_is_left_for_the_real_reader():
    """The guard only judges SIZE. A file that is not a zip at all is not
    its question to answer -- openpyxl's own error path (each adapter's
    AdapterError, or validation.py's generic except) already covers it."""
    guard_xlsx_bytes(b"not an excel file at all")  # must not raise


def test_an_empty_file_is_left_for_the_real_reader():
    guard_xlsx_bytes(b"")  # must not raise


# ---------------------------------------------------------------------------
# Each cap, isolated via monkeypatched (small) constants so the fixtures
# stay tiny and the tests stay fast, while exercising the exact same code
# path production traffic goes through.
# ---------------------------------------------------------------------------


def test_member_cap_refuses_a_large_low_ratio_member(monkeypatch):
    """Isolates the SIZE cap from the RATIO cap: near-incompressible filler
    keeps the ratio close to 1:1, so only the member-size check can fire."""
    monkeypatch.setattr(xlsx_guard, "MAX_MEMBER_DECOMPRESSED_BYTES", 20_000)
    monkeypatch.setattr(xlsx_guard, "MAX_COMPRESSION_RATIO", 1_000_000.0)  # neutralise the ratio cap
    monkeypatch.setattr(xlsx_guard, "MAX_TOTAL_DECOMPRESSED_BYTES", 1_000_000_000)

    content = _incompressible_bomb_bytes(30_000)
    with pytest.raises(XlsxBombError) as exc_info:
        guard_xlsx_bytes(content, label="member_cap.xlsx")
    assert exc_info.value.detail["code"] == "xlsx_member_too_large"
    assert "member_cap.xlsx" in str(exc_info.value)
    assert "cap 20,000" in str(exc_info.value)


def test_ratio_cap_refuses_a_highly_compressible_member_under_the_size_cap(monkeypatch):
    """A member well under the size cap can still be refused purely on how
    implausibly well it compresses -- the real bomb shape (a few hundred KB
    on disk expanding past the size cap) is caught here before it ever gets
    that large."""
    monkeypatch.setattr(xlsx_guard, "MAX_COMPRESSION_RATIO", 60.0)
    monkeypatch.setattr(xlsx_guard, "MAX_MEMBER_DECOMPRESSED_BYTES", 1_000_000_000)
    monkeypatch.setattr(xlsx_guard, "MAX_TOTAL_DECOMPRESSED_BYTES", 1_000_000_000)

    content = _bomb_bytes(5_000_000)  # ~1000:1 real ratio, well under both size caps
    with pytest.raises(XlsxBombError) as exc_info:
        guard_xlsx_bytes(content, label="ratio_cap.xlsx")
    assert exc_info.value.detail["code"] == "xlsx_compression_ratio_too_high"
    assert "ratio_cap.xlsx" in str(exc_info.value)


def test_ratio_cap_ignores_members_below_the_floor():
    """A tiny member's ratio is noise, not evidence -- must not false-positive.
    Uses the SHIPPED floor/cap (no monkeypatching): 1000 declared bytes is
    under ``_RATIO_CHECK_FLOOR_BYTES`` (4096), so even though its own ratio
    is enormous (a single repeated character), it is never evaluated."""
    assert 1000 < xlsx_guard._RATIO_CHECK_FLOOR_BYTES
    content = _bomb_bytes(1000)
    guard_xlsx_bytes(content)  # must not raise


def test_total_cap_refuses_many_members_each_under_the_member_cap(monkeypatch):
    monkeypatch.setattr(xlsx_guard, "MAX_MEMBER_DECOMPRESSED_BYTES", 50_000)
    monkeypatch.setattr(xlsx_guard, "MAX_COMPRESSION_RATIO", 1_000_000.0)
    monkeypatch.setattr(xlsx_guard, "MAX_TOTAL_DECOMPRESSED_BYTES", 120_000)

    src = zipfile.ZipFile(BytesIO(_tiny_workbook_bytes()))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            archive.writestr(info, src.read(info.filename))
        # Three near-incompressible members, each under the (patched) 50,000
        # byte member cap, summing to 90,000 * ... past the 120,000 total cap.
        import os

        for i in range(4):
            block = os.urandom(45_000).hex().encode("ascii")[:45_000]
            archive.writestr(f"xl/extra{i}.xml", block)
    content = out.getvalue()

    with pytest.raises(XlsxBombError) as exc_info:
        guard_xlsx_bytes(content, label="total_cap.xlsx")
    assert exc_info.value.detail["code"] == "xlsx_total_too_large"


def test_member_count_cap_refuses_many_tiny_members(monkeypatch):
    monkeypatch.setattr(xlsx_guard, "MAX_MEMBERS", 5)

    src = zipfile.ZipFile(BytesIO(_tiny_workbook_bytes()))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in src.infolist():
            archive.writestr(info, src.read(info.filename))
        for i in range(20):
            archive.writestr(f"junk/{i}.txt", "x")
    content = out.getvalue()

    with pytest.raises(XlsxBombError) as exc_info:
        guard_xlsx_bytes(content, label="many_members.xlsx")
    assert exc_info.value.detail["code"] == "xlsx_too_many_members"


# ---------------------------------------------------------------------------
# Production-scale sanity: a bomb built with the SHIPPED constants (not
# monkeypatched) is refused, with the measured numbers quoted in the message.
# ---------------------------------------------------------------------------


def test_a_production_scale_bomb_is_refused_with_the_measured_numbers_in_the_message():
    content = _bomb_bytes(5_000_000)  # small on disk, ~1000:1 -- trips MAX_COMPRESSION_RATIO for real
    with pytest.raises(XlsxBombError) as exc_info:
        guard_xlsx_bytes(content, label="real_bomb.xlsx")
    message = str(exc_info.value)
    assert "real_bomb.xlsx" in message
    assert "5,000,113" in message or "5,000," in message  # the declared byte count appears verbatim
    assert f"cap {xlsx_guard.MAX_COMPRESSION_RATIO:.0f}:1" in message


# ---------------------------------------------------------------------------
# Lying headers: not this guard's problem to diagnose -- the real reader's.
# ---------------------------------------------------------------------------


def test_a_lying_header_zip_is_not_silently_admitted_full_size():
    """The CENTRAL DIRECTORY declares a small size (so the header precheck
    sees nothing to refuse) but the recorded CRC is for a much larger real
    stream. The guard's streaming layer hits this mid-read: zipfile
    truncates to the declared size and then raises BadZipFile because the
    CRC does not match -- the guard treats that as "not a size problem,
    let the real reader see it" and returns normally. Prove the real reader
    (plain openpyxl, exactly as every adapter calls it) then refuses this
    file on its own -- so the bomb is never silently admitted at full size,
    it is just refused by the EXISTING corrupt-file path instead of a new
    bomb-specific one.
    """
    content = _lying_header_bytes(declared_small=1000, actual_large=200_000)

    guard_xlsx_bytes(content)  # does not raise -- defers to the real reader

    with pytest.raises(zipfile.BadZipFile):
        openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)


def test_a_declared_size_that_matches_reality_exactly_is_genuinely_harmless():
    """The non-lying shape, for contrast with the test above: a member whose
    declared size and CRC both honestly describe a small, complete,
    well-formed XML document (not a truncated prefix of something bigger).
    Nothing here is a lie, so both the guard and openpyxl agree it is a
    normal, small, readable member -- confirming the guard does not treat
    "small declared size" itself as suspicious, only an inconsistency.
    """
    content = _bomb_bytes(16)  # tiny, complete, valid -- well under every cap
    guard_xlsx_bytes(content)  # must not raise

    wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    assert wb is not None


# ---------------------------------------------------------------------------
# Regression (adversarial review, 2026-08-13): a member with the encryption
# general-purpose flag bit set (no size violation at all) made the guard's
# Layer 2 streaming loop's ``archive.open(info)`` raise a raw ``RuntimeError``
# ("... is encrypted, password required ...") that only ``except
# zipfile.BadZipFile`` did not catch -- escaping guard_xlsx_path/bytes and
# every adapter's narrower ``except`` into an unhandled crash. Confirmed
# regression: the exact same file opens with ZERO exceptions under plain
# ``openpyxl.load_workbook(read_only=True)``, because openpyxl never reads
# ``docProps/app.xml`` at all (empirically the only member, of this base
# workbook's 9, that neither read_only nor full-load openpyxl ever touches --
# every other member, including ones read_only mode is supposed to skip,
# turned out to be read unconditionally by ExcelReader.read()). The guard
# was therefore STRICTER than the real reader on a file the real reader
# considers perfectly fine -- exactly the false-positive shape this guard
# must never produce.
# ---------------------------------------------------------------------------


def _flag_member_as_encrypted(content: bytes, member_name: str) -> bytes:
    """Set the encryption bit (general-purpose flag bit 0) on ``member_name``
    in both its local file header and its central directory record, leaving
    every size/CRC field untouched -- this is a readability problem, not a
    size lie."""
    raw = bytearray(content)
    target = member_name.encode()
    idx = raw.find(target)
    patched = 0
    while idx != -1:
        local_start = idx - 30
        if local_start >= 0 and raw[local_start : local_start + 4] == b"PK\x03\x04":
            flags = struct.unpack_from("<H", raw, local_start + 6)[0]
            struct.pack_into("<H", raw, local_start + 6, flags | 0x1)
            patched += 1
        central_start = idx - 46
        if central_start >= 0 and raw[central_start : central_start + 4] == b"PK\x01\x02":
            flags = struct.unpack_from("<H", raw, central_start + 8)[0]
            struct.pack_into("<H", raw, central_start + 8, flags | 0x1)
            patched += 1
        idx = raw.find(target, idx + 1)
    assert patched == 2, f"fixture bug: expected to patch local+central headers for {member_name!r}"
    return bytes(raw)


def test_plain_openpyxl_never_touches_docProps_app_xml_fixture_sanity():
    """Fixture sanity check, not guard behaviour: confirms the premise --
    flagging docProps/app.xml as encrypted must NOT affect plain openpyxl at
    all, so the regression test below is actually testing what it claims."""
    content = _flag_member_as_encrypted(_tiny_workbook_bytes(), "docProps/app.xml")
    wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    assert wb.sheetnames


def test_an_encrypted_member_with_no_size_violation_does_not_raise_in_guard_xlsx_bytes():
    content = _flag_member_as_encrypted(_tiny_workbook_bytes(), "docProps/app.xml")
    guard_xlsx_bytes(content)  # must not raise -- and must NOT be a raw RuntimeError


def test_an_encrypted_member_with_no_size_violation_does_not_raise_in_guard_xlsx_path(tmp_path):
    content = _flag_member_as_encrypted(_tiny_workbook_bytes(), "docProps/app.xml")
    path = tmp_path / "encrypted_member.xlsx"
    path.write_bytes(content)
    guard_xlsx_path(path)  # must not raise -- and must NOT be a raw RuntimeError
