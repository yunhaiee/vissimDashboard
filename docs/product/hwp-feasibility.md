# HWP/HWPX report-generation feasibility — decision doc

**Status:** Phase 0.5 survey, 2026-08-03. Escalation-rule output (TK requirements v2.1, R4.3 /
open question 8): *"기존 도구 우선 활용; 기존 도구가 없거나 포맷 하나 추가에 과도한 시간이
들면 중단하고 사용자에게 보고 → 사용자가 결정."* Verdict: an existing tool is good enough that
**no escalation is needed** — this did not become a project of its own.

---

## Recommendation (read this, skip the rest if you're in a hurry)

1. **Target HWPX, not legacy HWP.** HWPX (OWPML, KS X 6101, ZIP+XML like DOCX/OOXML) is an open,
   documented format; legacy `.hwp` is closed binary. As of **2026-05-18 the Korean government
   made HWPX mandatory** for documents attached in the 온나라 system (central ministries, then
   local governments) — the direction of travel is *away* from `.hwp`, which strengthens the case
   for HWPX over chasing the harder, undocumented legacy format.
2. **Use `python-hwpx`** (Apache-2.0, pure Python, no Hancom Office required, actively
   maintained — v5.6.0 released today, 102 stars, 556 commits, 4 open issues). A bounded spike
   (below) generated a heading, a styled/colour-filled table, and an embedded PNG in valid OWPML
   XML, and the library's own schema validator reports **zero issues**. This is a normal
   `pip install`-able dependency — same shape as `python-docx`, which `report_builder.py` already
   uses.
3. **Cost estimate: small, bounded, not a project of its own.** A `services/report_builder_hwpx.py`
   sibling to today's `report_builder.py`, re-driving the same block spec into `python-hwpx` calls
   instead of `python-docx` calls, is realistically **3-6 focused days** for the DOCX report's
   existing block set (cover, GEH tables, LoS table, flow/GEH table, travel-time charts). Ongoing
   maintenance risk is low-to-moderate: it's a single small Alpha-tagged Apache-2.0 project by one
   or a few maintainers, not a corporate-backed SDK — pin the version and vendor/fork if it ever
   goes stale (see Unknowns).
4. **The template-fill path is real and probably better than generate-from-scratch.** `python-hwpx`
   ships `HwpxDocument.open()` + `fill_by_path()` / `fill_form_field()` / `find_cell_by_label()`
   specifically for "load an existing HWPX, fill labelled cells/fields, leave everything else
   byte-identical." If TK/Dochak can supply one real 교통영향평가 HWPX template, that is very
   likely the pragmatic R4.3 implementation for Korean output — closer to how the DOCX
   style-from-sample feature is already scoped, and it sidesteps having to hand-build Korean
   report layout conventions we don't have ground truth for.
5. **Fallback if you'd rather not spend even this:** keep DOCX-only and document a manual
   conversion step for the user (Hancom Office's own File > Open on a `.docx`, or the 한글
   "다른 이름으로 저장" to `.hwpx`, both round-trip acceptably for text/tables/images in practice
   per Hancom's own compatibility notes). This costs nothing to build but pushes a manual step onto
   every report delivery — the escalation rule's explicit fallback.

**What I would NOT do:** chase legacy binary `.hwp` generation (pyhwp is read-only and dead since
2016; there is no maintained write path), and I would not build on LibreOffice/unoconv (it has no
HWP export filter at all — dead end, verified below).

---

## Evidence table

| Candidate | Read | Write | Maintenance | License | Python | Verdict |
|---|---|---|---|---|---|---|
| **python-hwpx** (`airmang/python-hwpx`) | Yes | **Yes** | Active — v5.6.0 released today (2026-08-03), 102★, 33 forks, 556 commits, 4 open issues, Alpha tag | **Apache-2.0** (confirmed from installed package METADATA: `License-Expression: Apache-2.0`) | Pure Python 3.10+, only dep is `lxml<7,>=4.9` | **Chosen candidate.** Spiked successfully below. |
| pyhwpxlib | Yes | Yes (built on python-hwpx's model) | Frequent releases (0.18.3, 2026-05-10) | **Dual: PolyForm-Noncommercial-1.0.0 + Apache-2.0** — noncommercial by default | Pure Python, extras for images/lxml | Rejected — noncommercial license is a blocker for Dochak's proprietary commercial product; `python-hwpx` gives the same core capability cleanly under Apache-2.0. |
| rhwp-python (`DanMeon/rhwp-python`) | Yes (HWP+HWPX, fast) | Partial — round-trips text/paragraphs, explicitly does **not** guarantee tables/figures/formulas on write | Modest (13★, 187 commits, 0 open issues) | MIT | Prebuilt Rust wheels, no compiler needed | Good reader, not a safe writer for our table/figure-heavy reports. Worth revisiting only as a *reader* (e.g. ingesting a client-supplied HWP sample for R4.3 style extraction). |
| pyhwp / hwp5 / hwp5txt (`mete0r/pyhwp`) | Yes (HWP5 binary only) | **No** | **Discontinued since 2016** | GPL/varies | Python 2-era lineage | Not viable for writing; legacy binary parser only. |
| HWPX as hand-rolled ZIP+XML | Yes/Write, in principle | Yes, in principle | N/A (would be our own code) | N/A | N/A | The OWPML spec (KS X 6101, ratified 2011-12-30) is a public Korean national standard, and Hancom's own tech blog (tech.hancom.com/hwpxformat) documents the package layout in the open. This is *exactly* what `python-hwpx` already does well — reinventing it is the "포맷 하나 추가에 과도한 시간" the escalation rule warns about. Not recommended as a from-scratch effort given a maintained library exists. |
| Hancom COM automation (`pyhwpx` by martiniifun / `hwpapi` by JunDamin, via `pywin32`) | Yes | Yes (drives the real app, like Word COM) | Active small community projects | Varies (check per-repo) | Windows-only, **requires a licensed Hancom Office install** | **Hancom Office 2020 IS installed on this dev machine** (`C:\Program Files (x86)\Hnc\Office 2020\HOffice110\Bin`, `Hwp.exe` + `HwpCtrl.ocx` + `HwpAutomation.dll/tlb` all present — checked via registry `HwpFrame.HwpObject` CLSID and filesystem, not assumed). Viable *if* the backend host also carries a licensed Hancom install, but it inherits every problem Word-COM-on-a-server has (CLAUDE.md's own COM caution for Vissim applies by analogy: fragile, stateful, GUI-app-on-a-server smell) and adds a Hancom Office license cost per host. Not spiked (see Unknowns) — didn't want to launch a GUI app unannounced on the user's machine mid-survey. |
| Hancom's official SDK (`sdk.hancom.com`, HWP SDK) | Yes | Yes | Vendor-maintained | **Commercial, paid license** | Documented, cross-platform per vendor docs | Real option if `python-hwpx` proves insufficient at scale — but paid, and unnecessary right now given the OSS library already cleared the spike. |
| LibreOffice / unoconv DOCX→HWP conversion | — | — | — | — | — | **Dead end.** LibreOffice has no HWP import/export filter at all; unoconv can only bridge formats LibreOffice itself supports, and HWP isn't one of them. Verified via search, not assumed. |
| Commercial conversion services (web APIs) | Varies | Varies | Varies | Paid, per-document/API-call pricing | REST APIs | Exist (e.g. legalhub.co.kr HWP 변환 API) but mean sending report data to a third party for every generated report — a worse trust boundary than an in-process library for a product whose validation layer *is* the product. Not pursued further. |

---

## Spike results (bounded effort, throwaway venv)

**Environment:** `python -m venv` created at
`C:\Users\user\AppData\Local\Temp\claude\...\scratchpad\hwp-spike\venv311` — **entirely outside the
repo**, nothing installed into `backend/.venv`. Installed one package: `python-hwpx` (pulls in
`lxml` as its only dependency). This venv/spike directory is disposable; nothing here needs to
survive past this task.

**What the spike script did** (`scratchpad/hwp-spike/spike.py`, throwaway, not committed):
1. `HwpxDocument.new()` — created a blank document.
2. `add_paragraph("Prism Model Build Report (spike)")` — heading-shaped text.
3. `add_paragraph(...)` — a body paragraph.
4. `add_table(rows=2, cols=3)` then `set_cell_text(...)` for a Node/GEH/Grade header row + one
   data row, then `set_cell_shading(1, 2, "C6E0B4")` — the same green used for "good" GEH grade
   cells in today's DOCX report (`report_builder.py`'s `_GRADE_FILL`).
5. Built a minimal 4x4 red PNG by hand (no matplotlib needed for the spike) and
   `add_picture(png_bytes, "png", width_mm=40, height_mm=40)`.
6. `save_to_path(...)` — wrote `spike_out.hwpx`.

**Result: all four succeeded** (`heading=True table=True image=True`, file written, 8.9 KB).

**Verification performed (honest about what this does and doesn't prove):**
- `zipfile.testzip()` on the output → no corrupt member. The package structure matches the OWPML
  spec exactly: `mimetype` (`application/hwp+zip`), `META-INF/{container.xml,manifest.xml,
  container.rdf}`, `Contents/{content.hpf,header.xml,section0.xml}`, `BinData/BIN0001.png`,
  `Preview/{PrvImage.png,PrvText.txt}`, `settings.xml`, `version.xml`.
- Unzipped and inspected `Contents/section0.xml` directly: correct Hancom XML namespaces
  (`http://www.hancom.co.kr/hwpml/2011/paragraph` etc.), real `<hp:t>` text runs containing our
  exact strings ("Prism Model Build Report (spike)", "Node", "GEH", "Grade", "Node 12", "3.2",
  "Good"), two `<hp:tbl>` elements, two `<hp:pic>` elements, one reference to `BIN0001` (our PNG).
- **Round-trip + schema validation**: re-opened the file with `HwpxDocument.open()` (5 paragraphs
  read back, matching what was written) and called the library's own `validate()` method, which
  checks the output against the OWPML schema: `ValidationReport(validated_parts=('Contents/
  header.xml', 'Contents/section0.xml'), issues=())` — **zero schema issues**.
- **Verification limit, stated honestly:** I could not open the file in actual Hancom Office /
  한글 or a genuine HWPX viewer to visually confirm rendering. Hancom Office 2020 *is* installed on
  this machine (see COM automation row above), so a human could double-click `spike_out.hwpx` to
  eyeball it, but I have no screenshot/desktop-capture tool for a native GUI window in this
  environment, and launching a GUI app unannounced on the user's machine felt like the wrong call
  mid-survey. So: **structurally and schema-valid, not yet human-eyeballed.** This is the one gap
  a real go/no-go before shipping should close — takes five minutes with Hancom Office installed.

Spike artifacts (all disposable, outside the repo):
`C:\Users\user\AppData\Local\Temp\claude\C--Users-user-Documents-GitHub-vissimDashboard\46719c74-7a88-42fe-99c5-ad25f380332a\scratchpad\hwp-spike\` —
`spike.py`, `spike_out.hwpx`, `spike_chart.png`, `unzipped/`, `venv311/` (~40 MB).

---

## Mapping to what our reports actually need

`report_builder.py` + `report_exports.py` currently produce: a cover page with a styled control
table (merged cells, coloured header fill, white-on-navy text), ALL-CAPS Heading 1 / Heading 2
sections, `python-docx` "Caption" style table/figure captions, GEH/LoS/flow-validation tables with
per-cell colour grading (`_GRADE_FILL`, `_LOS_FILL`), and `matplotlib`-rendered PNG travel-time
charts embedded at a configurable width (`report_exports.render_travel_chart` /
`_add_travel_charts`'s `style.figure_width_in`).

Every one of those primitives has a confirmed `python-hwpx` equivalent (introspected directly on
the installed 5.6.0 API, not guessed from docs):
- Cell colour fill → `table.set_cell_shading(row, col, hex)` (spiked, worked).
- Cell/table borders → `table.set_cell_border_fill(...)`, `table.merge_cells(...)`.
- Page layout / margins → `doc.set_page_setup(paper_size=..., margins_mm=...)`,
  `doc.set_page_margins(...)`.
- Headers/footers, page numbers → `doc.set_header_content(...)`, `doc.set_page_number(...)`.
- Paragraph/heading styles → `doc.add_paragraph(..., style_id_ref=..., para_pr_id_ref=...)`,
  `doc.style(...)`, `doc.styles`.
- Embedded PNG figures at a controlled size → `doc.add_picture(bytes, "png", width_mm=..,
  height_mm=..)` (spiked, worked; note the API is metric-mm not inches, a real but small porting
  detail from today's `Inches(width_in)` calls).
- Template/style-from-sample (R4.3) → `HwpxDocument.open(path)` to load a client-supplied file,
  then `fill_by_path(...)` / `fill_form_field(...)` / `find_cell_by_label(...)` to populate it
  while leaving unedited bytes untouched — this is a closer fit to R4.3's "upload past report →
  extract conventions → fit new report to it" framing than generating from a blank canvas.

Net: a `report_builder_hwpx.py` would be a **parallel builder function per existing block type**,
not a rewrite of the data layer — `_geh_validation_rows`, `los_summary_rows`,
`_collect_flow_geh`, `travel_chart_specs` etc. all already separate "compute the numbers" from
"render into a document," which is exactly the seam a second renderer needs.

---

## The template-fill alternative (R4.3's real proposal), assessed

HWPX being ZIP+XML makes this straightforwardly plausible, and `python-hwpx` was **built for this
exact use case** — `fill_by_path()`'s design goal is literally "find cells by label, fill values,
leave everything else as original bytes" (byte-preservation of unedited regions is called out
explicitly in the library's own docs). This lines up with two things already true in this
codebase: (a) R4.3 already frames DOCX v1 as "extract layout/typography/figure conventions →
fit new report to it," and (b) the actual `.hwp`/`.hwpx` documents Korean traffic authorities
circulate today (검색으로 확인된 교통영향평가 체크리스트/지침 서식, currently distributed as
legacy `.hwp` by 국토교통부/katia.or.kr) are exactly the kind of fixed-layout, labelled-field
documents this API targets.

**This is the one place I'd actively steer the build toward**, over generating a Korean report
layout from scratch with no ground truth for the convention (spacing, terminology, section
ordering, official 도장/승인 blocks) that Korean 교통영향평가 reviewers expect.

---

## Honest unknowns, and what closes them

1. **We have never opened a real 교통영향평가 HWP/HWPX file.** Every claim about "matches what
   Korean agencies expect" is inferred from format specs and news coverage, not from a document in
   hand. **What resolves this:** the one real report TK/Dochak can supply (already an open item in
   `docs/product/tk-requirements-2026-07-31.md`'s open questions — 부록 A/B request to TK). If it's
   still `.hwp` (legacy binary, common in practice today per the katia.or.kr / 국토교통부 samples
   found in this search), that changes the calculus: we'd either need TK to supply a `.hwpx`
   version (Hancom Office can save any `.hwp` as `.hwpx` losslessly for this purpose), or reconsider
   the COM-automation path since python-hwpx does not write legacy `.hwp`.
2. **Whether HWPX is *accepted*, not just mandated for *internal* government channels.** The
   2026-05-18 mandate is specifically for documents attached inside 온나라 (the civil-servant
   internal document system) and 온메일 — strong directional evidence, but not direct proof that a
   consultant's *external* submission to a traffic authority must be HWPX rather than `.hwp` or
   PDF. **What resolves this:** ask TK directly what format 대전시/관할기관 currently *requires* on
   submission (a one-line question, cheap to ask alongside the 부록 A/B request already pending).
3. **Visual fidelity, unverified.** The spike proves schema-valid, structurally-correct output; it
   does not prove the rendered page *looks* right (font substitution, table auto-sizing, Korean
   text shaping — none of which the spike exercised with real Korean-language, multi-paragraph
   content). **What resolves this:** open `spike_out.hwpx` in Hancom Office (installed on this
   machine) for five minutes before committing real build time — cheap, hasn't been done here.
4. **`python-hwpx` is tagged Alpha by its own maintainers**, single/small-team project, no
   corporate backing. Low current risk (Apache-2.0 means we could fork and maintain it ourselves if
   abandoned — the format itself won't change under us since it's a ratified national standard),
   but worth a version pin and a note to re-check maintenance health before the TR-C-adjacent
   product ships broadly.
5. **Whether COM automation (Hancom Office actually installed here) is worth spiking properly.**
   Deliberately not exercised in this survey (see spike section) to avoid launching a GUI app
   unannounced. If `python-hwpx`'s fidelity gap ever turns out to matter, this is the fallback to
   spike next, on a machine/host that's expected to run it in production — which raises its own
   question (does the backend host get a Hancom Office license, given COM automation only works
   where Hancom Office is installed).

---

## Rules followed / broken

- No git commands run (hard rule 1) — confirmed, `git` was never invoked in this survey.
- No existing doc under `docs/` edited; this file is the one new file created (hard rule 2).
- No product code written; the only code written is the throwaway `scratchpad/hwp-spike/spike.py`
  outside the repo (hard rule 3).
- No packages installed into `backend/.venv` or anywhere inside the repo. One package
  (`python-hwpx`, pulling `lxml`) installed into a throwaway venv at
  `scratchpad/hwp-spike/venv311/`, entirely outside the repo (hard rule 4). Nothing else was
  installed anywhere.
- No dev servers restarted; backend/frontend product files were read-only throughout (hard rules
  4-5).
- **Verification:** I ran nothing against and changed nothing in the actual backend/frontend
  product code or its test suites — there was nothing to break. `pytest` / `npm run typecheck` /
  `npm run test:dashboard-contract` were not run because this unit touched none of the files they
  cover.
