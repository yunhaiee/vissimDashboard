# B4 데모 런북 v2 — 클릭 단위 (2026-08-12, 전 단계 브라우저)

Rewritten after `144ff96`+`63e5579`+`0d13102`+`eae4686`+`b3318ab`: every step is a browser
click except the one Vissim live-check (Part D), which is a single tool run on your box.
**No zip file anywhere in this flow** — the zip upload on the dashboard is the OLD dataset
path. Genesis needs only: a map region you draw numbers for (no file at all), and your 대전
counts workbook (`.xlsx`).

**Honest endpoint:** with demand only → `provisional` (V5 waits for signals). If you also do
Part E (현시도 confirm — 2 minutes), signals switch on and `published` is reachable after the
live check. Either way the demo proves the pipeline.

## Part 0 — before you start (once)

1. Both servers up. If a page won't load: backend `cd backend` →
   `.venv\Scripts\python.exe -m uvicorn main:app --port 8001`; frontend `cd frontend` →
   `npm run dev`. Hard-refresh (`Ctrl+Shift+R`) any tab that looks white-on-white — the theme
   fix landed today.
2. **Log in as yourself** (your real account — not any `*-acceptance` account). Left sidebar
   bottom, or `/login`.
3. Top-right **프로젝트** selector → **새 프로젝트** → make a demo project (e.g. `B4-데모`).
   **Never run this demo in the consultant project** — publishing replaces a project's active
   dataset.

## Part A — generate the geometry (browser, ~3 min + generation time)

1. Left sidebar → **네트워크 생성**.
2. In the first hint box, click **영역에서 추출하기 →**.
3. Enter the bounding box (WGS84 decimal degrees) in **North / South / West / East**.
   - Pick a box ~0.3–0.5 km around ONE intersection you have counts for (e.g. 가람네거리).
   - The limit panel updates live: it must say **Within the limit** before the button enables.
   - Lesson from your first run (bbox 36.3444–36.3564 / 127.3765–127.3925, REFUSED): a wide
     CBD box slices arterials mid-block and V3 correctly refuses the silent exits. Smaller,
     intersection-centred boxes cut fewer arterials. A V3 refusal is a *finding*, not a crash
     — the map shows you every silent-exit point in red.
4. **영역 추출** → wait for the green **EXTRACTED — ready to generate** card.
5. Name the network (optional), then start the generation from this page (it hands off to
   the same flow as an uploaded file). Go back via **← Generate from a file / see past
   generations** to watch it.
6. On **네트워크 생성**: your job appears under **생성 이력**, polls live, and the **Gate
   verdict** panel fills in. Expect on a geometry-only run: V1 · V5 · V6 = 확인 불가 (waiting
   on live check / signals / demand — by design), V3 = pass or fail depending on your box.
7. If REFUSED: the **지도에서 보기** map opens itself. Red = refuse-tier findings; click a red
   link → the right panel shows every finding on it. Fix = different box, not a weaker gate.
8. **Write down the entry link ids** for your counts intersection: zoom (wheel) / pan (drag)
   to the intersection, click each approach link that *enters* it, and note the link id shown
   in the detail panel. You need one id per approach leg (~4 numbers).

## Part B — build the demand input (browser, ~2 min)

1. Back on **네트워크 생성**, click **수요 입력 만들기 →**.
2. **1. 카운트 워크북 업로드**: **파일 선택** → one of the 11 real workbooks extracted at
   `client-data\유성구-교통량-워크북\` (e.g.
   `[지역 자치단체 제출용] 2026-04-06 00_00_2026-05-10 23_45_원신흥네거리1_15분_집계 주기.xlsx`
   — 원신흥네거리 is the best demo site: it also has a 현시도 sheet among the 23, so demand
   AND signals can both be real). Click **업로드**. NOTE: the region you generate must
   contain this intersection — find it on Naver/Kakao map and box it lat±0.002 / lon±0.0025.
   - The parsed summary appears: site, legs (e.g. 가람방면), vehicle classes, time window,
     and any per-row refusals by name. A wrong file refuses with a named reason — nothing
     proceeds on a bad parse.
3. **2. 접근로 → 링크 연결**: for each leg row, type the entry link id you noted in A-8.
   Empty cells stay empty — the build refuses rather than guessing.
4. Leave 구간 대푯값 at **peak** (labeled as a modelling choice) unless you know better.
5. **만들기** → a demand token card appears (with the document summary). If it refuses
   naming an unknown site/leg — a typo'd link id — fix the cell and click again.
6. **← 네트워크 생성으로**.

## Part C — the composed generation (browser, ~2 min)

1. Repeat A-2..A-4 for the SAME box (each generation consumes a fresh extract).
2. Before starting: in the job form, open **수요 입력 (선택)** and pick the token you built
   in Part B. (If you did Part E first, also pick the signals token here.)
3. **생성 시작**. On the finished job expect: **V3 pass, V6 pass**, V5 확인 불가 (no signals)
   — or, with signals attached, **blocking = V1 only**.
4. Open **Compare generations**, pick the Part-A job: watch V3/V6 flip from 확인 불가/fail —
   that's your before/after slide.
5. If the generation refuses naming `…mapped_approach_not_seeded`: one of your link ids
   points at a link that can't receive vehicles — re-read the map, fix that cell in Part B,
   rebuild the token, re-generate. The refusal names the exact site/leg/link.

## Part D — the live check (your Vissim box, ~5 min)

1. On the job, click **네트워크 .inpx** to download the artifact.
2. Close any open Vissim GUI (the tool opens its own instance; never attaches to yours).
3. In a terminal: `cd backend` then
   `.venv\Scripts\python.exe tools\genesis_open_live_check.py --help` — run it against the
   downloaded `.inpx`; it LoadNets the file, checks the `.err`, and writes a `state.json`.
4. Attach that `state.json` through the job's live-check control on the genesis page. This is
   recorded as YOUR attestation (name + timestamp, `rests_on_attestation` shown everywhere).
5. Verdict updates: demand-only → **provisional**; with signals too → **published**.

## Part E — optional but recommended: switch signals on (browser, ~3 min)

This is also, deliberately, C1-b's open acceptance: the first *real* confirmation click.

1. **네트워크 생성** → second hint box → **Review the 현시도 →** (`/genesis/phases`).
2. Left list: 23 intersections, each with the vision model's proposed reading. Pick yours
   (e.g. 가람네거리 if listed; any of the 20 plan-bearing ones works).
3. Check the proposal against the diagram image shown beside it — the model flagged its own
   uncertainty on nearly every phase; your eye is the judge. Correct anything wrong.
4. In the **확인** form: pick your Part-A/C generation → **노드 후보 불러오기** → pick the
   node → per-approach phase dropdowns → declare the amber (3.0 s per the sheets, with its
   basis) → **이 배정을 확인합니다**.
5. Left rail → **확인된 N건으로 만들기** → a signals token appears. Use it in Part C-2 on
   your next generation.

## Part F — simulate and report (browser)

1. The provisional/published network is now this project's active dataset. Go to
   **대시보드** and run a simulation exactly as you normally do.
2. **보고서 작성** → export the DOCX (ko/en/ja). The gate report, §5 compliance report, and
   `layers.json` are downloadable next to the artifact for appendices.
3. Done = one real Daejeon region went region→build→gate→(attested) →sim→report. Flip **B4 →
   ✅** in roadmap v2 §5.B with the date and region, and C1-b → ✅ if you did Part E.

## If anything refuses

Read the named reason — every refusal names its cause (file, site, leg, link, or check). Fix
the named input and rerun that part. A refusal you believe is WRONG is a finding: stop and
record it before touching anything.
