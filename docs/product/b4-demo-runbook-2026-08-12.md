# B4 데모 런북 — 생성 → 게이트 → 시뮬 → 리포트 (대전 소규모 실지역 1건)

2026-08-12. Prereq commits: `144ff96` (Track G wiring), `63e5579` (B3 UI), `0d13102` (V6 warn).
This is the user/COM lane — every step below runs on the Windows box with real Vissim.
**Honest endpoint, stated up front:** signals stay OFF (C1 — 현시도 not yet in machine-readable
form; C1-b queued), so V5 is honestly NOT_CHECKABLE and the generation lands at
**`provisional` after your V1 attestation, not `published`**. `published` needs the full layer
set. The demo still proves the Phase 3 exit sentence end-to-end: real region → gated build →
simulation → report.

## 0. Preconditions

- Backend on 8001, frontend on 3000 (`.claude/launch.json` configs `backend`/`frontend`).
- You are logged in (`/login`) with your operator account. All staging/start/attach calls are
  authenticated; keep the browser session, and for the API steps capture a bearer token.
- **Use a demo workspace, not the consultant workspace** (`bfc69251bd04` holds the client's
  calibrated dataset — publishing a generated network there would replace its active dataset).
  Create/select a demo workspace in the admin UI (P④) and note its `X-Workspace-Id`.

## 1. Region → .osm (browser, B2 page)

1. Open `http://localhost:3000/genesis/region`.
2. Enter a small central-Daejeon bounding box. The page shows the derived caps
   (`GET /api/genesis/region/limits`); the proven live fetch (`e9d16cf`) was ~0.36 km².
   **If you want demand attached later, choose a bbox containing one of the 11 Daejeon
   count-workbook intersections** (스마트교차로 drop).
3. Fetch. The Overpass result is staged as an upload token and lands in the same job-start
   flow B1 uses. Provenance (query, endpoint, date, ODbL) is embedded in the .osm itself.

## 2. First generation — geometry-only (browser)

Start the generation from the region page. This run is deliberately geometry-only:

- Watch it on `/genesis`: verdict panel renders V1–V9 tri-state; **if REFUSED, the B3 map
  opens itself** — rose = refuse-tier findings, amber = warn, excluded zones as a named list.
  This is your first look at the B3 UI on live data (the in-pane browser check was blocked by
  tooling on the dev box, so this step doubles as its visual verification).
- Expected verdict: `incomplete` → job `provisional`, blocking includes V1·V3·V5·V6
  (geometry-only can check none of the demand/signal-side refuse checks). V3 may fail on
  genuine base-map breaks — those are named per street; repairs would be road invention.

Use this run to read the **entry link ids** for your count site's approaches: click the
approaches on the map, note link ids (or download `genesis_json` from the artifact list).

## 3. Demand side-input (API — the UI form is a queued follow-up)

Build the demand document from the real Daejeon counts:

```python
# backend venv; produces demand-doc.json
from services.dataimport.adapters.daejeon import DaejeonCountAdapter
import json
counts = DaejeonCountAdapter().parse(["<path to the site's count workbook>"]).model_dump(mode="json")
doc = {
  "kind": "genesis.demand.v1",
  "counts": counts,
  # the human join, step 2's link ids: site -> leg -> ENTRY link id
  "approach_links": {"<site_id>": {"서측": 1, "동측": 3, "남측": 5, "북측": 7}},
  "demand_settings": {"single_interval_reduction": "peak"},
}
json.dump(doc, open("demand-doc.json", "w", encoding="utf-8"), ensure_ascii=False)
```

Stage it (PowerShell; `$tok` = your bearer token, `$ws` = demo workspace id):

```powershell
$r = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8001/api/genesis/side-inputs?kind=demand&filename=demand-doc.json" `
  -Headers @{ Authorization = "Bearer $tok"; "X-Workspace-Id" = $ws } `
  -ContentType "application/json" -InFile demand-doc.json
$r.summary   # sites_mapped / approaches_mapped must be non-zero
$demandToken = $r.token
```

A malformed document 400s with named reasons (`genesis_side_input_invalid_demand`); a mapped
approach that cannot be seeded **refuses the whole generation** later, naming site/leg/link —
that is the compose guard, working as intended. Fix the mapping, don't fight the refusal.

## 4. Second generation — composed (API)

Re-stage the same .osm (or fetch again) to get a fresh `upload_token`, then:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8001/api/genesis/jobs" `
  -Headers @{ Authorization = "Bearer $tok"; "X-Workspace-Id" = $ws } `
  -ContentType "application/json" -Body (@{
    upload_token = "<osm upload token>"
    name         = "daejeon_b4_demo"
    demand_token = $demandToken
  } | ConvertTo-Json)
```

On `/genesis`: the job record now carries `layers` (fleet/demand emitted, counts, refusals).
Expected gate: **V3 pass, V6 pass, V5 NOT_CHECKABLE** (no signals — honest), verdict
`incomplete` → `provisional`. Use the B3 **comparison panel** against the geometry-only run:
you should see V3/V6 flip from not-checkable, and any warn-tier deltas (including
`v6_composition_unused` if a composition ended up unreferenced — that would itself be a bug
report; expected 0).

## 5. V1 attestation — real Vissim opens the file (your COM lane)

1. Download the `.inpx` artifact from `/genesis` (authenticated).
2. Run the live check against it (closed Vissim GUI, no human dataset open — the tool uses
   `attach_to_running=False` discipline): see `backend/tools/genesis_open_live_check.py --help`
   for the artifact-path invocation; it LoadNets the file, checks `.err`, and writes
   `state.json` whose sha256 must match the gated artifact.
3. Attach it: `POST /api/genesis/jobs/{id}/live-check` with the `state.json` (auth). This is
   recorded as **operator attestation** — your name, the timestamp, `rests_on_attestation`
   visible wherever the verdict renders. V1 goes green; verdict stays `provisional`
   (V5 still not-checkable — correct).

## 6. Simulate

The provisional publish registered the network as the demo workspace's active dataset. From
the dashboard, run a simulation on it (the normal run flow — job queue, RunContinuous). An
unsignalized geometry+demand network simulates; vehicle inputs carry the real Daejeon counts
(peak-reduced single interval — D2's 15-min profiles remain measured-and-waiting).

## 7. Report

Export the audit-trail DOCX (1.3 flow, ko/en/ja) from the dashboard. It carries the run and
its decisions; the gate's ko report and `layers.json` are downloadable beside the artifact for
the appendix. (Style-from-sample, 2.6, is still open — this is the current report surface.)

## What "done" looks like

One real Daejeon region went: polygon → Overpass .osm (provenance embedded) → composed
geometry+demand+fleet .inpx → R1.5 gate verdict rendered and explained on a map → your
attested live LoadNet → provisional dataset → simulation results → DOCX. When this has run
once, flip **B4 → ✅** in roadmap v2 §5.B with the date and the region used.

## If something refuses

That is the product working. Read the named reason (job record `refusal.reasons`, ko), fix
the input it names, and re-run. Do not weaken a gate, do not skip a step — and if a refusal
looks WRONG, that is a finding: record it in DECISIONS.md before touching anything.
