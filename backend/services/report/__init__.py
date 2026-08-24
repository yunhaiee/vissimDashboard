"""Track R — the report engine (docs/product/report-engine-design-2026-08-24.md).

Layer 1 (this package's first module, :mod:`services.report.spine`) is the
regulation-derived CONTENT SPINE: the ordered sections MRWA's Operational
Modelling Guidelines require of a modelling report, transcribed with page
citations, plus the Table 5-15 outputs each section owes and an honest
availability marker for every one of them.

Deliberately a package and not one more ``services/report_*.py`` module: the
design doc's layers 2 and 3 (도판 생성기, 스타일 프로파일) are separate units
that will land beside this one, and ``services/report_builder.py`` /
``services/report_exports.py`` are the PRE-EXISTING v1 .docx assembler — they
keep their names and their behaviour, and nothing here imports them.
"""
