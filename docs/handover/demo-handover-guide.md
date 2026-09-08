# Prism 데모 인수인계 가이드 — Windows + Vissim PC용

*(English version follows the Korean one — scroll to "Prism demo handover guide".)*

작성 2026-09-04 · 대상: Vissim이 설치된 Windows PC를 가진 연구실 동료 · 작성자 이윤하(원격)
이 문서 하나로, 이 프로젝트를 처음 보는 사람이 **설치 → 실행 → Vissim 연결 확인 → 시뮬레이션 1회 →
지역 생성 파이프라인 라이브 체크**까지 끝낼 수 있게 썼습니다. 위에서 아래로 순서대로만 따라오면 됩니다.

---

## 0. 시작 전에 (3분)

**받은 것 세 가지**

| 받은 것 | 어디에 쓰나 |
|---|---|
| 코드 묶음 (`vissimDashboard-*.zip`) 또는 GitHub 접근권 | §2에서 폴더로 풉니다 |
| `ANTHROPIC_API_KEY` (별도 메시지로 전달) | §3에서 `backend\.env`에 붙여넣습니다 |
| 이 문서 | 지금 읽는 것 (코드 안 `docs/handover/`에도 있음) |

**"끝났다"의 기준 — 아래 4개 중 3개 이상**

1. 브라우저 `http://localhost:3000`에 대시보드가 뜬다 (Vissim 불필요)
2. `04-check-vissim.cmd`가 끝까지 돌고 `Disconnected/exited the dedicated Vissim instance.`로 끝난다
3. PTV 예제 네트워크로 시뮬레이션 1회가 상태 `done` 이 된다
4. (선택) 지도에서 만든 네트워크가 "실 Vissim으로 확인"을 통과한다

**절대 하지 말 것**

- `backend\.env`나 API 키를 채팅·이메일·깃에 올리지 않기. 키는 그 파일 안에만.
- 시뮬레이션·확인 버튼을 누를 때 **Vissim 창을 열어 두지 않기.** 자동화는 자기 Vissim 인스턴스를
  따로 띄우고 끝나면 닫습니다. 열려 있는 창에 붙지는 않지만, 인스턴스 수 제한(약 4개)에 걸릴 수 있습니다.
- 코드 폴더를 OneDrive/구글드라이브 동기화 폴더나 한글·공백이 든 경로에 두지 않기. `C:\dev\vissimDashboard` 권장.
- 이 패키지에는 **회사 클라이언트 데이터가 없습니다.** 합성 데이터와 PTV 설치본 예제만 씁니다. 외부에서 받은
  `.inpx`/엑셀을 올리지 마세요.

---

## 1. 준비물 체크리스트

- [ ] Windows 10/11, **로그인된 화면 세션** (원격 데스크톱 OK, 화면 잠금 상태에서는 Vissim COM이 동작하지 않음)
- [ ] **PTV Vissim 2026** (COM 포함 라이선스). 확인: `C:\Program Files\PTV Vision\PTV Vissim 2026` 폴더와
      `C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\` 폴더가 있는가.
      Vissim을 한 번 직접 열었다 닫아 라이선스가 활성화된 상태인지 확인.
- [ ] **Python 3.11 또는 3.12** — python.org 설치 시 **"Add python.exe to PATH" 체크**. (3.14는 아직 패키지 휠이 없어 실패)
- [ ] **Node.js 22 LTS** — nodejs.org
- [ ] 인터넷 (설치 패키지, Claude API, 지도 타일, 지역 추출)
- [ ] 여유 디스크 5 GB 이상 (시뮬레이션 1회 결과 = 수십 MB)
- [ ] (선택) Git — zip을 받았다면 필요 없음

---

## 2. 코드 받기 (5분)

**A안 — zip을 받은 경우**: `C:\dev\` 폴더를 만들고 zip을 그 안에 풀어 `C:\dev\vissimDashboard\` 가 되게 합니다.
**B안 — GitHub 접근권을 받은 경우**: `C:\dev`에서 `git clone https://github.com/yunhalee2008/vissimDashboard.git`

확인: `C:\dev\vissimDashboard\` 안에 `backend`, `frontend`, `scripts\windows`, `docs` 폴더가 보이면 됩니다.

---

## 3. 1회 설치 (10~25분, 대부분 기다리는 시간)

1. `scripts\windows\01-setup.cmd` **더블클릭**. 검은 창이 뜨고 6단계가 순서대로 돕니다:
   Python 가상환경 → 백엔드 패키지 → 프런트엔드 패키지 → 합성 데모 데이터(`data\`) → `backend\.env` 생성 → 계정 목록.
   끝에 `Setup complete.` 가 나오면 성공. `[X]` 로 멈추면 창의 글자를 전부 복사해서 윤하에게 보내주세요.
2. `backend\.env` 를 메모장으로 열어 `ANTHROPIC_API_KEY=` 뒤에 받은 키를 붙여넣고 저장. (따옴표 없이, 공백 없이)
3. `scripts\windows\06-create-admin.cmd` 더블클릭 → 아이디, 표시 이름, 비밀번호(두 번, 화면에 안 보임) 입력.
   이 제품에는 기본 계정(admin/admin 같은 것)이 **없습니다.** 여기서 만든 계정으로만 로그인할 수 있습니다.

> 왜 스크립트인가: PowerShell 실행 정책 때문에 `npm`이 막히는 PC가 많습니다. `.cmd` 파일은 그 문제를 피해 갑니다.
> 터미널을 직접 쓰고 싶다면 `README.md`의 "로컬 실행" 절과 동일한 명령입니다.

---

## 4. 실행하기 (매번 2분)

1. `02-start-backend.cmd` 더블클릭 → 창에 `Application startup complete.` 가 보이면 준비 완료. **창을 닫지 마세요.**
2. `03-start-frontend.cmd` 더블클릭 → `Ready` 또는 `Local: http://localhost:3000` 이 보이면 준비 완료. **이 창도 유지.**
3. 브라우저(Chrome/Edge)에서 `http://localhost:3000` 을 엽니다. 처음 한 번은 화면이 뜨는 데 10~20초 걸립니다.
4. **로그인**: 오른쪽 위 `로그인` → §3-3에서 만든 계정. 로그인하면 같은 자리에 이름과 `로그아웃` 이 보입니다.
5. **데모 프로젝트 만들기**: 왼쪽 사이드바 맨 위 `프로젝트` 옆 `+` → 이름 `데모` → 만들기 → 목록에서 `데모` 선택.
   앞으로 모든 업로드·실행은 이 프로젝트 안에서만 합니다. (`기본 프로젝트`에는 아무것도 올리지 않기 — 습관입니다.
   업로드 한 번이 그 프로젝트의 활성 데이터셋을 통째로 바꿉니다.)
6. 참고: 오른쪽 위 해/달 아이콘은 밝은/어두운 테마 전환, 지구본은 한국어/English/日本語 전환입니다.

멈추기: 두 검은 창을 닫으면 됩니다. 데이터는 `data\`와 `runtime\` 폴더에 남습니다.

---

## 5. 데모 A — Vissim 없이 화면 확인 (5분)

새 프로젝트는 업로드가 없으면 §3에서 만든 **합성 데모 데이터셋**을 씁니다. 그래서 바로 화면이 찹니다.

| 왼쪽 메뉴 | 보이면 정상 |
|---|---|
| ③ 시뮬·보정 → **대시보드** | `시나리오 비교 세트`에 `Base (AM Peak)`, `Signal Optimization`, `Demand +20%` 3개, 아래로 KPI 카드와 교차로 지도 |
| ① 네트워크 → **네트워크 뷰** | 두 교차로가 있는 작은 네트워크 도면 |
| ① 네트워크 → **모델 검진** | 네트워크 파일을 파싱한 점검 결과 (COM 없이 파일만 읽음) |
| ③ 시뮬·보정 → **보정** | 위에 하늘색 배너 `채점할 실행을 선택하세요` — 아직 실행이 없으니 그대로 두면 됩니다 |
| 오른쪽 아래 **AI** 버튼 | 어시스턴트 서랍. `차량 입력 목록을 보여줘` 라고 물어보면 읽어서 답합니다 (API 키가 들어 있을 때) |

여기까지 되면 §0의 기준 1 달성입니다.

---

## 6. 데모 B — Vissim 연결 확인 + 시뮬레이션 1회 (15분)

### 6-1. COM 스모크 테스트

1. 열려 있는 Vissim 창을 **모두 닫습니다.**
2. `04-check-vissim.cmd` 더블클릭 → 아무 키 → 진행 상황 `[1/7] … [7/7]` 이 찍힙니다. 이 도구는 자기 Vissim을
   띄우고, PTV 예제 `Urban Intersection Beijing.CN` 을 `runtime\com-smoke\` 에 복사해 열고, 60초를 돌리고,
   값을 되읽어 검증한 뒤 Vissim을 닫습니다. 1~3분 걸립니다.
3. 판정: 마지막에 `[7/7] Read-back + persistence verification` 다음 `Disconnected/exited the dedicated Vissim
   instance.` 로 끝나면 **성공** (기준 2). `Traceback`, `MISMATCH`, `No running VISSIM COM instance` 가 보이면 §8로.

### 6-2. 브라우저에서 시뮬레이션 1회

합성 데모 네트워크(`practice.inpx`)는 화면용이라 **Vissim이 열 수 없습니다.** 실행에는 PTV 예제를 씁니다.

1. 왼쪽 ③ 시뮬·보정 → **대시보드** → 오른쪽 `모델 선택` 카드 → `파일/ZIP 업로드` → 파일 선택 창에서
   `C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN\` 안의
   `.inpx` 파일을 선택. 카드가 `업로드됨: …` 으로 바뀝니다. (`.zip`이 아니라 `.inpx` 하나여도 됩니다.)
   대시보드의 비교 화면이 비어 보이는 것은 정상입니다 — 이 네트워크에는 미리 계산된 결과가 없습니다.
2. 왼쪽 ③ 시뮬·보정 → **시뮬레이션**. 위쪽 `실행 환경` 카드가 `실행 가능` · `VISSIM 실행 환경이 준비되었습니다.` · `환경: win32 / pywin32 OK` 여야 합니다.
   `실행 불가` 면 백엔드가 이 PC에서 도는지, §6-1이 통과했는지 확인.
3. `실행 설정`: `결과 이름` 아무거나(예 `beijing-test-1`), `실행 시간` 300 (초), `랜덤 시드` 42 → **`시뮬레이션 실행`**.
4. 아래 `최근 실행 작업` 에 작업이 생기고 진행률이 올라갑니다. Vissim 창은 보이지 않는 채로 돕니다(정상).
   300초 시뮬레이션은 보통 1~2분. 작업 카드의 상태 뱃지가 `done` 이 되면 기준 3 달성입니다 (`error` 면 §8).
5. 끝나면 대시보드 `모델 선택` → `기본값 복원` 으로 합성 데이터셋으로 되돌려 두면 §5 화면이 다시 찹니다 (선택).

---

## 7. 데모 C — 지도에서 네트워크를 만들고 실 Vissim으로 확인 (15분)

전체 절차서는 `docs/product/b4-demo-runbook-2026-08-12.md` 입니다. 이 패키지에는 실측 워크북과 현시도(클라이언트
데이터)가 없으므로 **Part A(지오메트리 생성)와 Part D(라이브 체크)만** 진행합니다. Part B·C·E·F는 윤하가 원격으로 합니다.
아래는 그 두 파트를 현재 화면 기준으로 다시 쓴 것입니다. (절차서의 "로그인은 왼쪽 아래", "프로젝트는 오른쪽 위"는
옛 위치입니다 — 지금은 §4-4, §4-5 위치.)

1. 왼쪽 ① 네트워크 → **생성** (`/genesis`) → `영역에서 추출하기 →`.
2. 지도에서 교차로 **하나**를 둘러싸는 다각형을 그립니다: 꼭짓점을 차례로 클릭, 마지막에 더블클릭으로 닫기.
   한 변 300~500 m 정도면 충분합니다. 영역이 한도를 넘으면 `영역 추출` 버튼이 꺼지고 그 이유가 버튼에 적힙니다 — 더 작게 다시 그리면 됩니다.
3. `영역 추출` → 초록 카드(추출 완료)가 뜨면 이름을 적고 생성을 시작합니다.
4. 생성 목록(`생성 이력`)에서 작업을 열면 **게이트 판정 V1~V9** 가 채워집니다. 지오메트리만 있는 생성에서
   `V1 · V5 · V6 = 확인 불가` 는 설계된 결과입니다(라이브 체크·신호·수요가 아직 없음). `V3/V4 실패` 는 실제 결함이며
   **이유가 문장으로** 적혀 있습니다 — 그대로 캡처해 두면 됩니다. 지도에서 빨간 링크를 클릭하면 소견이 보입니다.
5. **Part D — 라이브 체크**: 그 작업 화면의 **`실 Vissim으로 확인`** 버튼을 누릅니다. (Vissim 창은 닫아 두기.)
   서버가 자기 Vissim을 띄워 이 파일을 실제로 열어 보고, 결과로 판정을 다시 내립니다. 1~3분.
   `실 Vissim이 이 파일을 열었습니다 — 위의 판정은 그 결과로 다시 내려졌습니다.` 가 보이면 **기준 4 달성**.
   `열지 못했습니다` 로 끝나도 그것이 결과입니다 — 문구와 판정을 캡처해 주세요. 버그가 아니라 "거부가 제품"입니다.
6. 참고(수동 대안): 같은 검사를 콘솔에서 하려면 작업의 `네트워크 .inpx` 를 내려받은 뒤 `backend\` 에서
   `.venv\Scripts\python.exe tools\genesis_open_live_check.py --only murdoch --murdoch "C:\경로\내려받은.inpx" --label-suffix _demo`
   → 결과는 `runtime\genesis-open-live-check\state.json`. 보통은 5번 버튼이면 충분합니다.

---

## 8. 문제가 생기면

| 증상 | 조치 |
|---|---|
| `01-setup.cmd`가 pip 단계에서 빨간 글씨로 실패 | Python 버전 확인(`py -3 --version`). 3.14면 3.12로 재설치. 회사 프록시면 `pip config set global.proxy …` 후 재실행 |
| `npm` 이 "실행 정책" 오류 | 스크립트는 `npm.cmd` 를 쓰므로 안 나야 정상. 직접 칠 땐 PowerShell 대신 **cmd** 창 사용 |
| `02-start-backend.cmd` 가 바로 닫힘 / `address already in use` | 이전 백엔드 창이 살아 있음. 모두 닫고 다시 |
| 브라우저에 `프로젝트 정보를 불러오지 못했습니다` | 백엔드가 꺼져 있음. `02` 창 확인, `http://localhost:8001/docs` 가 열리는지 확인 |
| 화면이 하얗게 깨짐 / 옛 화면 | `Ctrl+Shift+R`. 그래도면 프런트 창 닫고 `frontend\.next` 폴더 삭제 후 `03` 다시 |
| 로그인 안 됨 | 계정이 없음(기본 계정 없음). `06-create-admin.cmd`. 이미 admin이 있어 거부되면 `06` 대신 `backend\.venv\Scripts\python.exe tools\manage_users.py reset-password --username 아이디` |
| `04` 에 `No running VISSIM COM instance was found` / 라이선스 오류 | Vissim을 직접 한 번 열어 라이선스 확인 후 닫고 재시도. 화면이 잠겨 있으면 안 됨. 작업 관리자에서 남은 `vissim.exe` 종료 |
| `04` 에 `ModuleNotFoundError: win32com` | `backend\.venv\Scripts\python.exe -m pip install pywin32` 후 재시도 |
| 시뮬레이션 실행이 `The network file could not be loaded` 로 실패 | 합성 네트워크를 돌리려 한 것. §6-2-1처럼 PTV 예제를 먼저 업로드 |
| 시뮬레이션 페이지가 `실행 불가` | 백엔드가 Windows에서 돌고 있는지(`환경: win32`), pywin32가 설치됐는지 |
| AI 서랍이 "제공자 없음"/응답 없음 | `backend\.env` 의 키 확인(따옴표·공백 없이), 백엔드 재시작 |
| 지역 추출이 오래 걸리거나 실패 | 인터넷(OSM 서버) 문제. 더 작은 영역으로 재시도 |
| 데모 데이터를 처음 상태로 | `01-setup.cmd` 재실행이 안전합니다 (`data\Scenarios` 재생성, 계정·업로드는 그대로) |

**건강 확인 한 방**: `05-run-tests.cmd` → 백엔드 `0 failed`(skipped는 정상), 프런트 `fail 0`. 하나라도 다르면 창 텍스트를 보내주세요.

---

## 9. 윤하에게 보내줄 것

- [ ] `04-check-vissim.cmd` 창 전체 텍스트 (특히 `Connected via VISSIM.Vissim.2026 …` 줄과 마지막 줄), Vissim 버전(도움말→정보)
- [ ] 시뮬레이션 1회: `최근 실행 작업` 카드 캡처(결과 이름·상태·소요 시간)
- [ ] (했다면) 생성 작업 화면 캡처: V1~V9 판정 + 라이브 체크 문구
- [ ] 이상했던 것 전부, **화면 문구 그대로** — 거부 문구는 특히. "왜 안 되지"의 원문이 곧 버그 리포트입니다
- [ ] 걸린 시간 (설치 / 스모크 / 시뮬레이션)

---

## 10. (보내는 사람용) 패키지 만드는 법

Mac에서, 저장소 루트에서:

```bash
git archive --format=zip --prefix=vissimDashboard/ -o ~/Desktop/vissimDashboard-$(date +%Y%m%d).zip HEAD
```

`git archive`는 **추적 중인 파일만** 담습니다 — `backend/.env`, `data/`, `runtime/`, `client-data/`, `node_modules/`,
`.venv/` 는 자동으로 빠집니다. 보내기 전에 `unzip -l … | grep -E "\.env$|client-data|runtime/"` 로 비어 있는지 한 번 확인.

보낼 것: ① 위 zip ② `ANTHROPIC_API_KEY` (zip과 다른 채널로) ③ 이 문서 링크.
보내지 말 것: 클라이언트 데이터, `runtime/`(과거 세션·업로드), 본인 `.env`.
GitHub 접근권 부여는 별도 결정입니다 — 저장소는 Dochak 소유의 비공개 코드입니다.

---
---

# Prism demo handover guide — for a Windows PC with Vissim

Written 2026-09-04 · Audience: a lab colleague who has PTV Vissim on a Windows PC · Author: Yunha Lee (remote)
This one document takes someone who has never seen the project from **install → run → Vissim connection check →
one simulation → live check of the region-to-network pipeline**. Follow it top to bottom.

---

## 0. Before you start (3 min)

**The three things you received**

| Item | Used in |
|---|---|
| The code bundle (`vissimDashboard-*.zip`) or GitHub access | §2, unpacked into a folder |
| `ANTHROPIC_API_KEY` (sent in a separate message) | §3, pasted into `backend\.env` |
| This document | Now (also inside the code at `docs/handover/`) |

**What "done" means — at least 3 of these 4**

1. The dashboard opens at `http://localhost:3000` (no Vissim needed)
2. `04-check-vissim.cmd` runs to the end and finishes with `Disconnected/exited the dedicated Vissim instance.`
3. One simulation on the PTV example network reaches status `done`
4. (optional) A network generated from the map passes "Check in a real Vissim"

**Never**

- Put `backend\.env` or the API key into chat, email or git. It lives only in that file.
- Leave a Vissim window open while pressing a run or check button. The automation launches its **own** Vissim
  instance and closes it afterwards; it never attaches to yours, but the per-machine instance limit (~4) can bite.
- Put the code folder inside OneDrive/Google Drive sync, or a path with spaces or non-ASCII characters. `C:\dev\vissimDashboard` is recommended.
- This package contains **no client data.** Only synthetic data and the examples shipped with Vissim are used. Do not upload
  `.inpx` or workbooks received from elsewhere.

---

## 1. Prerequisites checklist

- [ ] Windows 10/11 with a **logged-in desktop session** (Remote Desktop is fine; a locked screen stops Vissim COM)
- [ ] **PTV Vissim 2026** with a COM-capable licence. Check that `C:\Program Files\PTV Vision\PTV Vissim 2026` and
      `C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\` exist. Open Vissim once by hand and close it,
      so the licence is known to be active.
- [ ] **Python 3.11 or 3.12** — from python.org, tick **"Add python.exe to PATH"** in the installer. (3.14 has no wheels yet and fails.)
- [ ] **Node.js 22 LTS** — from nodejs.org
- [ ] Internet access (package installs, the Claude API, map tiles, region extraction)
- [ ] 5 GB or more free disk (one simulation writes tens of MB)
- [ ] (optional) Git — not needed if you received the zip

---

## 2. Get the code (5 min)

**Option A — you received a zip**: create `C:\dev\` and unpack so the result is `C:\dev\vissimDashboard\`.
**Option B — you received GitHub access**: in `C:\dev`, run `git clone https://github.com/yunhalee2008/vissimDashboard.git`

Check: `C:\dev\vissimDashboard\` contains `backend`, `frontend`, `scripts\windows` and `docs`.

---

## 3. One-time setup (10–25 min, mostly waiting)

1. **Double-click** `scripts\windows\01-setup.cmd`. A black window runs six steps in order:
   Python venv → backend packages → frontend packages → synthetic demo data (`data\`) → `backend\.env` → account list.
   `Setup complete.` at the end means success. If it stops with `[X]`, copy all the text in the window and send it to Yunha.
2. Open `backend\.env` in Notepad, paste the key after `ANTHROPIC_API_KEY=` and save. (No quotes, no spaces.)
3. Double-click `scripts\windows\06-create-admin.cmd` → enter a username, a display name and a password (twice, not echoed).
   There is **no default account** (no admin/admin). The account you create here is the only way to sign in.

> Why scripts: on many PCs PowerShell's execution policy blocks `npm`. The `.cmd` files sidestep that.
> If you prefer a terminal, the commands are the same as the "로컬 실행" section of `README.md`.

---

## 4. Running it (2 min, every time)

1. Double-click `02-start-backend.cmd` → ready when the window shows `Application startup complete.` **Keep the window open.**
2. Double-click `03-start-frontend.cmd` → ready when it shows `Ready` or `Local: http://localhost:3000`. **Keep this one open too.**
3. Open `http://localhost:3000` in Chrome or Edge. The very first page takes 10–20 s to compile.
4. **Sign in**: top-right `로그인` → the account from §3-3. Once signed in, your name and `로그아웃` appear in that spot.
5. **Create a demo project**: top of the left sidebar, `프로젝트` → `+` → name it `데모` → create → select it in the list.
   Every upload and run from now on happens inside this project. (Never upload into `기본 프로젝트` — a habit worth keeping:
   one upload replaces that project's active dataset.)
6. Note: the sun/moon icon at the top right switches light/dark theme; the globe switches 한국어/English/日本語.

To stop: close the two black windows. Data stays in `data\` and `runtime\`.

---

## 5. Demo A — the screens, without Vissim (5 min)

A new project with no upload uses the **synthetic demo dataset** from §3, so the screens fill immediately.

| Left menu | Looks right when |
|---|---|
| ③ 시뮬·보정 → **대시보드** (Dashboard) | `시나리오 비교 세트` shows `Base (AM Peak)`, `Signal Optimization`, `Demand +20%`; KPI cards and an intersection map below |
| ① 네트워크 → **네트워크 뷰** (Network view) | A small two-intersection network drawing |
| ① 네트워크 → **모델 검진** (Model health check) | A check report parsed from the network file (no COM, file only) |
| ③ 시뮬·보정 → **보정** (Calibration) | A sky-blue banner `채점할 실행을 선택하세요` — there are no runs yet, leave it |
| **AI** button, bottom right | The assistant drawer. Ask `차량 입력 목록을 보여줘` and it reads the network and answers (when the key is set) |

That is criterion 1 from §0.

---

## 6. Demo B — Vissim connection check + one simulation (15 min)

### 6-1. COM smoke test

1. **Close every Vissim window.**
2. Double-click `04-check-vissim.cmd` → any key → progress `[1/7] … [7/7]` prints. The tool launches its own Vissim,
   copies the PTV example `Urban Intersection Beijing.CN` into `runtime\com-smoke\`, loads it, runs 60 s, reads values back
   to verify them, and closes Vissim. Takes 1–3 minutes.
3. Verdict: **success** (criterion 2) when it ends with `[7/7] Read-back + persistence verification` followed by
   `Disconnected/exited the dedicated Vissim instance.` If you see `Traceback`, `MISMATCH` or `No running VISSIM COM instance`, go to §8.

### 6-2. One simulation from the browser

The synthetic demo network (`practice.inpx`) is for the screens only — **Vissim cannot open it.** Runs use the PTV example.

1. Left ③ 시뮬·보정 → **대시보드** → right-hand `모델 선택` card → `파일/ZIP 업로드` → in the file dialog pick the `.inpx` inside
   `C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN\`. The card changes to
   `업로드됨: …`. (A single `.inpx` is fine; no zip needed.) The dashboard's comparison panels look empty for this network —
   normal, it has no precomputed results.
2. Left ③ 시뮬·보정 → **시뮬레이션**. The `실행 환경` (Runtime) card near the top must read `실행 가능` · `VISSIM 실행 환경이 준비되었습니다.` · `환경: win32 / pywin32 OK`.
   If it says `실행 불가`, check that the backend runs on this PC and that §6-1 passed.
3. `실행 설정`: `결과 이름` anything (e.g. `beijing-test-1`), `실행 시간` 300 (seconds), `랜덤 시드` 42 → **`시뮬레이션 실행`**.
4. A job appears under `최근 실행 작업` and its progress climbs. No Vissim window is visible while it runs (normal).
   A 300 s simulation usually takes 1–2 minutes. When the job card's status badge reads `done` you have criterion 3 (`error` → §8).
5. Afterwards, dashboard `모델 선택` → `기본값 복원` puts the synthetic dataset back so §5 fills again (optional).

---

## 7. Demo C — build a network from the map and check it in a real Vissim (15 min)

The full runbook is `docs/product/b4-demo-runbook-2026-08-12.md`. This package has no counts workbook and no signal sheets
(client data), so you do **Part A (geometry) and Part D (live check) only**; Parts B, C, E and F are Yunha's, remotely.
Below is those two parts rewritten for the current screens. (The runbook's "sign in at the bottom left" and "project at the top
right" are old positions — see §4-4 and §4-5.)

1. Left ① 네트워크 → **생성** (`/genesis`) → `영역에서 추출하기 →`.
2. On the map, draw a polygon around **one** intersection: click the corners in turn, double-click to close.
   Sides of 300–500 m are plenty. If the area exceeds the limit, the `영역 추출` button disables and states why on the button itself — redraw smaller.
3. `영역 추출` → when the green "extracted" card appears, give it a name and start the generation.
4. Open the job from the list (`생성 이력`); the **gate verdict V1–V9** fills in. On a geometry-only generation
   `V1 · V5 · V6 = 확인 불가` (not checkable) is by design — no live check, signals or demand yet. A `V3/V4` failure is a real
   defect and **its reason is written out** — just capture it. Clicking a red link on the map shows the finding.
5. **Part D — live check**: on that job's page press **`실 Vissim으로 확인`** (Check in a real Vissim). (Vissim windows closed.)
   The server launches its own Vissim, really opens the file, and re-issues the verdict from the result. 1–3 minutes.
   `실 Vissim이 이 파일을 열었습니다 — 위의 판정은 그 결과로 다시 내려졌습니다.` means **criterion 4**.
   If it ends with `열지 못했습니다` (could not open), that is also a result — capture the wording and the verdict. Not a bug:
   a named refusal is the product.
6. Manual alternative: to run the same probe from a console, download the job's `네트워크 .inpx`, then from `backend\`:
   `.venv\Scripts\python.exe tools\genesis_open_live_check.py --only murdoch --murdoch "C:\path\to\downloaded.inpx" --label-suffix _demo`
   → result in `runtime\genesis-open-live-check\state.json`. Step 5's button is normally all you need.

---

## 8. If something goes wrong

| Symptom | What to do |
|---|---|
| `01-setup.cmd` fails in red at the pip step | Check the Python version (`py -3 --version`). If 3.14, install 3.12. Behind a company proxy: `pip config set global.proxy …`, then rerun |
| `npm` complains about "execution policy" | The scripts use `npm.cmd`, so this should not happen. If typing by hand, use a **cmd** window, not PowerShell |
| `02-start-backend.cmd` closes at once / `address already in use` | A previous backend window is still alive. Close them all and retry |
| Browser says `프로젝트 정보를 불러오지 못했습니다` | The backend is down. Check the `02` window; `http://localhost:8001/docs` should open |
| Page is blank/broken or shows an old screen | `Ctrl+Shift+R`. If that fails, close the frontend window, delete `frontend\.next`, run `03` again |
| Cannot sign in | No account exists (there is no default). Run `06-create-admin.cmd`. If it refuses because an admin exists: `backend\.venv\Scripts\python.exe tools\manage_users.py reset-password --username NAME` |
| `04` says `No running VISSIM COM instance was found` / licence error | Open Vissim by hand once to confirm the licence, close it, retry. The screen must not be locked. Kill leftover `vissim.exe` in Task Manager |
| `04` says `ModuleNotFoundError: win32com` | `backend\.venv\Scripts\python.exe -m pip install pywin32`, then retry |
| A run fails with `The network file could not be loaded` | You ran the synthetic network. Upload the PTV example first (§6-2-1) |
| Simulation page says `실행 불가` | Is the backend running on Windows (`환경: win32`)? Is pywin32 installed? |
| The AI drawer says no provider / never answers | Check the key in `backend\.env` (no quotes/spaces), restart the backend |
| Region extraction hangs or fails | Internet / OSM server. Retry with a smaller area |
| Reset the demo data | Rerunning `01-setup.cmd` is safe (`data\Scenarios` regenerated; accounts and uploads untouched) |

**One-shot health check**: `05-run-tests.cmd` → backend `0 failed` (skipped is normal), frontend `fail 0`. Anything else: send the window text.

---

## 9. What to send back to Yunha

- [ ] The whole text of the `04-check-vissim.cmd` window (especially the `Connected via VISSIM.Vissim.2026 …` line and the last line), and the Vissim version (Help → About)
- [ ] The simulation: a capture of the `최근 실행 작업` card (name, status, duration)
- [ ] (if done) The generation job page: the V1–V9 verdict and the live-check sentence
- [ ] Everything that looked odd, **in the screen's own words** — refusal messages especially. The verbatim "why won't it" *is* the bug report
- [ ] Time taken (setup / smoke test / simulation)

---

## 10. (Sender) Building the package

On the Mac, from the repository root:

```bash
git archive --format=zip --prefix=vissimDashboard/ -o ~/Desktop/vissimDashboard-$(date +%Y%m%d).zip HEAD
```

`git archive` includes **tracked files only** — `backend/.env`, `data/`, `runtime/`, `client-data/`, `node_modules/` and `.venv/`
are left out automatically. Before sending, confirm with `unzip -l … | grep -E "\.env$|client-data|runtime/"` that nothing matches.

Send: ① the zip ② the `ANTHROPIC_API_KEY` (through a different channel than the zip) ③ a link to this document.
Do not send: client data, `runtime/` (past sessions and uploads), your own `.env`.
Granting GitHub access is a separate decision — the repository is Dochak's private, proprietary code.
