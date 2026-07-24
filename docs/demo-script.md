# M4 데모 스크립트 — 에이전트 모드 전체 흐름
*(Demo script: full agent-mode walkthrough, for recording)*

발표자용 리허설/녹화 대본입니다. ko 본문 + en 주석(이탤릭) 형식으로, 클릭/발화와 화면에
나타나는 결과를 번호순으로 짝지었습니다.
*(Presenter script. Korean body text with English annotations. Each numbered step pairs
what the presenter says/clicks with what should appear on screen.)*

이 문서는 실제 라이브 브라우저 검증(2026-07-24, 이 저장소의 개발 서버 + Claude API
provider)에서 캡처한 실제 화면 텍스트를 예시로 포함합니다 — 발표 중 정확히 같은 문구가
나온다는 보장은 아니지만(LLM 응답이라 매 실행마다 문구가 조금씩 달라짐), 실제로 검증된
흐름과 형태입니다.
*(This script includes real captured UI text from an actual live-browser verification run
(2026-07-24). The exact wording will vary turn to turn since it's an LLM answer, but the
flow and shape shown here are genuinely verified, not invented.)*

---

## 0. 사전 준비 (Prerequisites) — 먼저 읽으세요

### 0.1 두 개의 서로 다른 데이터셋이 필요합니다

| 단계 | 필요한 데이터셋 | 이유 |
|---|---|---|
| 1–4단계 (읽기, 애매한 편집 요청, 확인, 승인 카드) | 기본 합성 데모 데이터셋 (`demo_project.zip`, 대시보드 기본값) 그대로 사용 가능 | 프리플라이트/섀도우 모델 검사는 `.inpx`를 파싱만 하면 되므로 실제 Vissim COM이 필요 없습니다. |
| 5단계 이후 (실제 승인 실행, 검증된 답변, 실행 요청, 짧은 실행 경고) | **반드시 Vissim COM으로 로드 가능한 네트워크로 교체해야 합니다.** 합성 데모 네트워크는 COM으로 열리지 않습니다. | *(The synthetic demo network cannot be COM-loaded — verified live, see §0.2.)* |

**검증된 사실**: 이번 라이브 점검에서, 합성 데모 데이터셋인 상태로 승인(`실행`)을 누르면
매번 다음 COM 오류로 실패합니다 (백엔드 로그 원문):
*(Verified fact: with the synthetic demo dataset active, clicking "실행"/Run always fails
with this exact COM error — captured verbatim from the backend log:)*

```
pywintypes.com_error: (-2147352567, 'Exception occurred.',
  (0, 'VISSIM.Vissim.2600', 'The network file could not be loaded.', None, 0, -2147352567), None)
```

에이전트는 이 오류를 붙잡아 "제 쪽 값 설정 문제가 아니라 Vissim이 네트워크 파일을
로드하는 과정에서 실패한 것으로 보입니다"라고 정직하게 설명합니다 — UI가 멈추거나
콘솔 오류를 내지 않고, 반복 한도(K=6) 안에서 깔끔하게 종료됩니다. 이 자체가
가드레일 원칙("완료된 잘못된 답보다 이유가 분명한 거부가 낫다")의 좋은 예시이지만,
**5단계 이후를 실제로 보여주려면 네트워크를 교체해야 합니다.**
*(The agent catches the failure and explains it honestly instead of hanging or crashing —
itself a good illustration of the guardrail doctrine, but you must swap networks to actually
demo steps 5+.)*

### 0.2 네트워크 교체 절차 — Beijing 예제 (Reference: DECISIONS.md + tools/*_live_check.py)

Phase 2 개발 중 라이브 COM 검증에 실제로 사용된 네트워크는 Vissim 2026 설치본에 포함된
"Urban Intersection Beijing.CN" 예제입니다 (`docs/phase2/DECISIONS.md`의 2026-07-23
`M2.1`/`M2.3` 항목, `backend/tools/com_smoke_test.py`, `backend/tools/edit_tool_live_check.py`,
`backend/tools/run_gate_live_check.py`가 모두 이 네트워크를 기본값으로 사용합니다).

1. 대시보드 우측 상단 "모델 선택" 카드에서 **"파일/ZIP 업로드"**를 클릭합니다.
2. 파일 선택 창에서 다음 경로의 `.inpx` 파일을 선택합니다 (zip이 아니라 단일 `.inpx`
   업로드도 지원됩니다 — `backend/services/datasets.py:save_active_dataset`):
   ```
   C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN\*.inpx
   ```
3. 업로드가 끝나면 "모델 선택" 카드가 "업로드됨: <파일명>"으로 바뀝니다 — 이제 에이전트의
   모든 새 세션은 이 네트워크를 기준으로 클론합니다.
4. **주의**: 대시보드 비교/KPI 화면은 이 네트워크에 대한 사전 계산된 시나리오 결과가
   없으므로 비어 보일 수 있습니다 — 정상입니다. 이 데모는 에이전트 패널에만 집중하세요.
5. 데모가 끝나면 "기본값 복원" 버튼으로 합성 데모 데이터셋으로 되돌리세요.

*(§0.2 in English: swap the active dataset to the "Urban Intersection Beijing.CN" example
that ships with the Vissim 2026 install, via the dashboard's existing "Upload File/ZIP"
control — a single `.inpx` file upload is supported, no zip needed. This is the exact
network every M2 live-check tool (`com_smoke_test.py`, `edit_tool_live_check.py`,
`run_gate_live_check.py`) already uses; it is not a new/invented path.)*

### 0.3 Beijing 네트워크의 실제 객체 번호를 모르는 상태로 시작하세요 — 일부러

발표자는 Beijing 네트워크의 정확한 vehicle input 번호를 외울 필요가 없습니다. 아래 1단계
자체가 "차량 입력 목록을 보여줘"이므로, 에이전트가 실제 번호를 읽어서 알려줍니다 — 그 번호를
그대로 이후 단계에서 사용하면 됩니다. (합성 데모 네트워크에서 라이브 검증했을 때 이 패턴이
정확히 이렇게 동작했습니다 — 아래 1~2단계 참고.)

*(Don't memorize Beijing's real object numbers in advance — step 1 itself asks the agent to
list them, and you read the real number off the answer for later steps. Verified live on the
synthetic network in exactly this shape; Beijing will differ in the specific numbers but not
in the pattern.)*

### 0.4 리허설 캐리어 경고 (Rehearsal caution — read before recording)

라이브 점검 중, 승인(`/agent/approve`) 직후 바로 이어지는 후속 질문이 **다른 세션 ID로
새로 시작**되는 사례를 재현했습니다 — 요청 바디에는 올바른 `session_id`가 담겨 있었는데도
서버 응답이 다른 값을 돌려주는 경우였습니다 (프런트엔드 코드·백엔드 라우터 코드 모두
확인했으나 원인이 명확히 재현되지 않는 환경적 현상으로 보임 — 상세는 이 작업의 최종
보고서 참고). 이 경우 7단계("이어지는 경고")가 나타나지 않고 새 대화처럼 시작됩니다.
**반드시 녹화 전에 7단계를 최소 1회 리허설로 확인**하고, 재현되면 그 턴을 다시 보내
보세요(보통 재시도하면 정상적으로 이어집니다).
*(Live testing twice reproduced a case where the turn immediately after an approve() call
started a fresh session server-side despite the correct session_id being sent — root cause
not isolated within this task's scope; flagged for the controller. Rehearse step 7 before
recording; a retry typically threads correctly.)*

---

## 본 스크립트 (The script)

### 1단계 — 패널 열기 + 에이전트 모드 켜기
**클릭**: 우측 하단 AI 버튼 → 패널이 열림 → 헤더의 "에이전트 모드 (베타)" 체크박스 클릭.

**화면에 나타나는 것**: 체크박스가 켜지고, "AI 엔진 준비됨" / "COM 준비됨" 상태 pill과
"대화 초기화" 버튼이 헤더에 보입니다.

*(Open the panel, toggle "Agent mode (beta)" on. Status pills + the reset button appear.)*

---

### 2단계 — 읽기 질문 (Read question)
**발화/입력**: "이 네트워크의 차량 입력 목록을 보여줘" *(Show me the vehicle inputs in this
network.)*

**화면에 나타나는 것** (합성 데모 네트워크로 실제 캡처한 예):
```
1. 차량 입력 목록 조회 중
2. 차량 입력 목록 3개 확인, 나머지 확인 필요

현재 네트워크의 차량 입력은 총 6개입니다:
1. No.1 - Main St EB (링크 101)
2. No.2 - Main St WB (링크 207)
3. No.3 - 1st Ave SB (링크 103)
나머지 3개 입력의 상세 정보가 데이터에 잘려 있어 확인이 필요합니다...
```
단계 서술(narration)이 회색 소문자로 답변 위에 먼저 뜨고, 이어서 답변 본문이 스트리밍됩니다.
Beijing 네트워크에서는 정확한 번호/이름이 다르게 나옵니다 — 여기서 나온 번호를 4단계에서
그대로 사용하세요.

*(Step narration renders in small gray text above the streamed answer. Note the real object
number/name Beijing returns here — you'll reuse it in step 4.)*

---

### 3단계 — 편집 요청, 일부러 애매하게 (Edit request — deliberately ambiguous)
**발화/입력**: "차량 입력 107을 6000으로 바꿔줘" *(Change vehicle input 107 to 6000.)*
(107은 Beijing 네트워크에 존재하지 않을 가능성이 큰 번호를 일부러 골라, `object_not_found`
확인 질문 흐름을 보여주기 위한 것입니다.)

**화면에 나타나는 것** (실제 캡처):
```
1. 차량 입력 107 확인 중
2. 차량 입력 목록을 확인하는 중
3. 차량 입력 107을 찾을 수 없어 확인 요청

차량 입력 번호 "107"은 네트워크에서 찾을 수 없었습니다. 현재 네트워크에는 다음 6개의
차량 입력이 있습니다 (일부만 표시됨):
1: Main St EB (링크 101)
2: Main St WB (링크 207)
3: 1st Ave SB (링크 103)
그 외 3개
혹시 말씀하신 "107"이 이 중 하나의 링크 번호이거나 다른 입력을 가리키는 것인지
확인해 주시겠어요?
```
에이전트가 후보 목록과 함께 명확한 확인 질문으로 답합니다 — 추측해서 엉뚱한 입력을
바꾸지 않습니다. `object_not_found` 가드레일(preflight, refuse) 덕분입니다.

*(The agent asks a clarifying question with real candidates instead of guessing —
`object_not_found`, a refuse-tier preflight constraint. This IS the answer, not a crash.)*

---

### 4단계 — 확인에 답하기 → 승인 카드 (칩 포함) (Clarify → approval card with chips)
**발화/입력**: 2단계에서 확인한 실제 번호를 사용해 "1번 차량 입력을 말한거예요, 6000으로
바꿔줘" *(I meant vehicle input No.1 — set it to 6000.)*

**화면에 나타나는 것** (실제 캡처, M4에서 새로 추가된 칩 포함):
```
1. 차량 입력 1번을 확인하는 중
2. 차량 입력 1(Main St EB)의 교통량을 6000으로 변경 (승인 대기)

다음 작업을 실행하려면 승인이 필요합니다:
set_vehicle_input_volume: 차량 입력 1(Main St EB)의 교통량을 6000으로 변경
⚠ 요청한 볼륨 6000 veh/h가 링크 '101'(차로 3개)의 대략적 용량 5700 veh/h를
  초과합니다. 초과 수요는 화면에 보이지 않는 잠재 수요로 대기하게 됩니다.
승인하시겠습니까?

┌ 실행 전 확인이 필요합니다 ────────────────
│ 차량 입력 교통량 변경              [주의 1]  ← M4 칩
│ 차량 입력 1 → 6000 veh/h
│ 요청한 볼륨 6000 veh/h가 링크 '101'(차로 3개)의 대략적 용량 ...
│ [실행]  [취소]
└────────────────────────────────────
```
**발표 포인트**: 액션 행 오른쪽의 amber `주의 1` 칩이 이번 M4 작업의 핵심 UI입니다 —
경고가 없으면 회색 `통과` 칩이 대신 뜹니다. 이 칩은 `approval.warnings`(백엔드가 이미
보내던 값)에서 계산되며, 거부(refuse)된 액션은 애초에 이 카드까지 올 수 없으므로
"거부" 색의 칩은 존재하지 않습니다 — 백엔드 코드로 확인: 프리플라이트가 차단(blocking)
판정을 내리면 `orchestrator.py`가 승인 카드를 만들기 전에 이미 거부 답변으로 종료합니다.

*(Presenter point: the amber "주의 1"/"Warn 1" chip next to the action row is this
milestone's key new UI — a clean action shows a neutral "통과"/"Pass" chip instead. There is
deliberately no "refuse" chip color: a blocking preflight finding ends the turn as a refusal
BEFORE the approval card is ever built — verified directly against
`backend/services/agent/orchestrator.py`.)*

이 단계는 여기까지는 합성 데모 데이터셋으로도 완전히 재현됩니다(승인 카드 생성은
섀도우 모델만 있으면 되므로 COM이 필요 없습니다). **5단계부터는 §0.2의 Beijing
교체가 필요합니다.**

---

### 5단계 — 승인 (Approve)
**클릭**: 승인 카드의 "실행" 버튼.

**Beijing 네트워크로 교체된 상태에서 화면에 나타나는 것** (기대되는 형태 — 코드 기준,
`backend/services/agent/tools_edit.py:set_vehicle_input_volume`의 반환값 형태를 따름):
```
1. 차량 입력 1번의 교통량을 6000으로 변경합니다
2. 쓰기 검증 완료 안내

차량 입력 1의 볼륨을 6000 veh/h로 변경했습니다. COM으로 다시 읽어 확인한 값도
6000.0 veh/h로 일치합니다 (읽기-쓰기 검증 통과).
```
(실제 문구는 매 실행마다 LLM이 조금씩 다르게 표현합니다 — 핵심은 **쓴 값과 다시 읽은
값이 둘 다 답변에 등장**한다는 점입니다. 승인 카드는 "승인됨"이라는 회색 라벨로
바뀌고 버튼은 사라집니다.)

**합성 데모 데이터셋으로 교체하지 않았다면** 대신 다음이 나타납니다 (실제 캡처):
```
1. 차량 입력 1번의 값을 6000으로 변경합니다 (오류)
2. 차량 입력 변경 실행 중 오류 발생을 안내

차량 입력 1번의 볼륨을 6000으로 변경하려 했지만 실행 중 오류가 발생했습니다.
오류 내용: VISSIM COM 오류 — "네트워크 파일을 불러올 수 없습니다
(The network file could not be loaded)."
```

*(§0.2's swap is required for a genuine success here — otherwise the exact COM failure
text above appears instead, which is itself a legitimate (if less exciting) thing to show:
the guardrail doctrine holding even when the underlying platform fails.)*

---

### 6단계 — 검증된 답변 (읽기-쓰기 증거) (Verified answer with read-back evidence)
5단계의 성공 답변 자체가 6단계입니다 — 별도 클릭 없이, "쓴 값"과 "다시 읽은 값"이
둘 다 답변에 포함되어 있는지 화면에서 짚어 주세요. 이것이 "Vissim은 잘못된 편집을
거의 거부하지 않고 확신에 찬 틀린 숫자를 낸다"는 문제에 대한 이 프로덕트의 핵심
대응입니다 (`docs/phase2/00-EXECUTION-GUIDE.md` §4).

*(No separate click — narrate over step 5's success answer, pointing out that both the
written value and the COM read-back value appear together. This is the product's core
answer to "Vissim rarely refuses a bad edit and reports confident wrong numbers.")*

---

### 7단계 — 같은 세션에서 후속 질문: 이어지는 경고 + 확인 기억 (Follow-up in the SAME
session: carried-forward warning + clarification memory)

**발화/입력**: 새 메시지로 (승인/거절 후, 세션을 초기화하지 않은 채) "방금 바꾼 차량
입력의 현재 네트워크 요약을 간단히 알려줘" *(Summarize the current network, including what
I just changed.)*

**화면에 나타나야 하는 것 (기대 형태 — 코드 기준)**:
1. **확인 기억(clarification memory)**: 답변이 "1번" 같은 재확인 질문 없이 곧바로 이전에
   확정한 차량 입력 1을 참조합니다 — 3~4단계에서 해소한 모호함을 다시 묻지 않습니다.
   *(라이브 검증됨: 3~4단계와 완전히 동일한 패턴으로, 클라리피케이션 답변이 바로 다음
   턴의 계획에 반영되는 것을 이 저장소에서 직접 확인했습니다.)*
2. **이어지는 경고(carried-forward warning)**: 답변 맨 아래, 여백 한 줄 뒤에 옅은 회색
   박스(M4에서 새로 추가한 스타일)로 다음과 같은 블록이 나타납니다:
   ```
   참고: 이전 대화에서 발생한 경고가 여전히 유효합니다. 이번 답변도 아래 내용과
   함께 참고하세요:
   - 요청한 볼륨 6000 veh/h가 링크 '101'(차로 3개)의 대략적 용량 5700 veh/h를
     초과합니다. 초과 수요는 화면에 보이지 않는 잠재 수요로 대기하게 됩니다.
   ```
   이 블록은 본문과 시각적으로 분리됩니다(구분선 + 옅은 배경) — M4 전에는 그냥
   이어지는 문단이었습니다.

**주의**: §0.4의 리허설 경고를 참고하세요 — 이 턴이 새 세션으로 잘못 시작되면 이 경고
블록이 나타나지 않습니다. 리허설에서 반드시 먼저 확인하세요.

*(Expected, code-verified shape: the answer should reference vehicle input 1 without
re-asking (clarification memory — directly verified live in this repo, same pattern as
steps 3–4), and a visually distinct muted callout block starting with "참고:"/"Note:" should
appear below the main answer (the M4 notice-block styling). See §0.4's caution — rehearse
this step first.)*

---

### 8단계 — 실행 요청 (Run request)
**발화/입력**: "5분만 시뮬레이션 돌려줘" *(Run a 5-minute simulation.)*

**화면에 나타나는 것 (기대 형태)**: run 티어도 write와 동일하게 승인 카드가 먼저
뜹니다 (`run_simulation`도 `_GATED_TIERS`에 포함) — 액션 행은 "시뮬레이션 실행"
레이블과 지속시간/시드 요약을 보여줍니다. 승인 후 시뮬레이션 잡이 기존 잡 큐를 통해
실행됩니다 (`RunContinuous`, `docs/phase2/DECISIONS.md`의 M2.4 항목).

*(Run tier is gated exactly like write — an approval card appears first, action row labeled
"Run simulation" with duration/seed summary. After approval it submits to the existing job
queue via RunContinuous.)*

---

### 9단계 — 짧은 실행 경고 (Short-run warning)
5분(300초 미만 평가 구간)처럼 짧은 실행은 결과 DB에 SIMULATIONRUN 테이블만 남고 KPI
테이블이 비어 있습니다 — `results_missing_kpi_tables` 경고가 발동해야 합니다.

**라이브로 이미 검증된 사실** (`docs/phase2/DECISIONS.md`, 2026-07-23 M2.4 항목,
Beijing clone 기준): "edit → 300s run through the real job queue via RunContinuous →
SIMULATIONRUN row verified → the `results_missing_kpi_tables` warning FIRED on a genuine
short run — the detector proven against real Vissim behavior." 이 데모 단계는 그 실제
검증을 다시 재현하는 것입니다 — 새로 발명한 동작이 아닙니다.

*(Already verified live per DECISIONS.md's M2.4 entry — this demo step reproduces a
previously-proven behavior, not a new claim.)*

---

## 부록 A — 각 단계에 필요한 것 요약 (What each step needs)

| 단계 | 라이브 Vissim 필요? | Beijing 교체 필요? |
|---|:---:|:---:|
| 1 패널/에이전트 모드 켜기 | 아니오 | 아니오 |
| 2 읽기 질문 | 아니오 (섀도우 모델만) | 아니오 |
| 3 애매한 편집 요청 → 확인 | 아니오 | 아니오 |
| 4 확인 응답 → 승인 카드(칩) | 아니오 | 아니오 |
| 5 승인 실행 | **예** | **예** |
| 6 검증된 답변(읽기-쓰기 증거) | **예** | **예** |
| 7 이어지는 경고 + 확인 기억 | 아니오 (텍스트 자체는), 그러나 6단계가 성공해야 경고가 존재함 | 6단계가 성공했어야 함 |
| 8 실행 요청 → 승인 카드 | 아니오(카드까지는) | 아니오 |
| 9 짧은 실행 경고 | **예** | **예** |

## 부록 B — 이 문서가 참조한 근거 (Sources, not invented)

- Beijing 예제 경로: `backend/tools/com_smoke_test.py`, `backend/tools/edit_tool_live_check.py`,
  `backend/tools/run_gate_live_check.py`의 `DEFAULT_EXAMPLE`.
- 합성 데모 네트워크가 COM으로 로드되지 않는다는 사실: 이 M4 작업의 라이브 브라우저
  검증에서 직접 재현 (백엔드 로그에 `VISSIM.Vissim.2600 ... could not be loaded` 그대로
  기록됨).
- 짧은 실행 경고의 라이브 검증: `docs/phase2/DECISIONS.md`, "2026-07-23 — M2.4 decision
  batch" 항목 맨 아래 "Live acceptance" 문단.
- 읽기-쓰기 검증 값 형태(`written_value`/`readback_value`): `backend/services/agent/
  tools_edit.py`의 `set_vehicle_input_volume` 반환값.
- 승인 카드가 거부된 액션을 절대 보여주지 않는다는 사실:
  `backend/services/agent/orchestrator.py`의 `if preflight_report.blocking:` 분기(승인
  카드를 만드는 `pending_approval = {...}` 대입보다 먼저 return함).
