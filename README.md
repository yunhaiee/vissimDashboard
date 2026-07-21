# Prism Dashboard Source Code

## 개요

Dochak Prism은 VISSIM 교통 시뮬레이션 파일과 결과를 웹에서 조회·비교·분석하는 로컬 대시보드입니다.
사용자가 브라우저에서 대시보드를 열면 Next.js 프론트엔드가 화면을 구성하고, FastAPI 백엔드가 VISSIM 데이터와 분석 결과를 읽어 API로 전달합니다.

이 폴더에는 웹 대시보드를 유지보수하는 데 필요한 프론트엔드와 백엔드 소스만 포함되어 있습니다. VISSIM 원본 파일, 실행 결과, 설치 프로그램, 라이선스/Keygen 코드, 패키징 파일은 제외했습니다.

## 동작 흐름

1. 사용자가 `http://localhost:3000`에서 Prism 대시보드를 엽니다.
2. 프론트엔드는 화면별로 `frontend/lib/api.ts`를 통해 백엔드 API를 호출합니다.
3. 백엔드는 VISSIM 프로젝트·시나리오·결과 파일을 읽고 지도, KPI, 교차로/방향별 지표 데이터를 반환합니다.
4. 프론트엔드는 지도, 비교표, 차트, 보고서 화면으로 결과를 표시합니다.

## 주요 기능

- 베이스모델 검증: 관측 교통량과 시뮬레이션 결과를 지도와 지표로 비교
- 대시보드: 시나리오 선택, 교차로 선택, KPI·방향별 교통량·사고/이벤트 정보 확인
- 네트워크: VISSIM 네트워크 및 지도 기반 교차로 탐색
- 시뮬레이션: 시나리오 생성·실행 및 실행 결과 조회
- 데이터 다운로드: 분석 결과와 데이터셋 파일 내보내기
- 보고서 작성: 분석 결과 기반 보고서 생성 및 다운로드
- AI 어시스턴트: VISSIM 매뉴얼/RAG 기반 질의응답
- 다국어 화면: 한국어, 영어, 일본어 지원

## 폴더 및 주요 파일

| 위치 | 역할 |
| --- | --- |
| `frontend/app` | 페이지 진입점과 전역 스타일. `base-model`, `dashboard`, `network`, `simulation`, `downloads`, `report` 화면을 포함 |
| `frontend/components` | 지도, KPI, 시나리오 선택, 교차로·방향별 표, 차트, AI 어시스턴트 등 UI 컴포넌트 |
| `frontend/lib/api.ts` | 프론트엔드에서 백엔드 API를 호출하는 공통 코드 |
| `frontend/lib/i18n*.ts` | 화면 다국어 문구와 언어 처리 |
| `frontend/package.json` | Next.js 기반 프론트엔드 의존성 및 실행 명령 |
| `backend/main.py` | FastAPI 서버 시작점 및 API 라우터 등록 |
| `backend/routers` | 데이터셋, 시나리오, 시뮬레이션, 지도, 보고서, 다운로드, AI API 엔드포인트 |
| `backend/services` | VISSIM 파일 해석, 시뮬레이션 실행, 결과 분석, 지도 데이터 생성, 보고서·RAG 처리 로직 |
| `backend/config.py` | 백엔드 경로 및 환경 설정 |
| `backend/requirements.txt` | 백엔드 Python 의존성 |

## 로컬 실행

먼저 Python과 Node.js를 설치합니다. 터미널을 두 개 열어 백엔드와 프론트엔드를 각각 실행합니다.

```powershell
# 터미널 1: 백엔드
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

```powershell
# 터미널 2: 프론트엔드
cd frontend
npm install
npm run dev
```

- 웹 대시보드: `http://localhost:3000`
- 백엔드 API 문서: `http://localhost:8001/docs`

## 참고

실제 VISSIM 파일을 이용한 분석·시뮬레이션 기능은 PTV Vissim 설치 환경과 프로젝트 데이터 경로가 필요합니다. 이 소스 폴더는 코드 관리와 웹 기능 수정용이며, 데이터와 설치형 실행 파일은 별도 보관합니다.
