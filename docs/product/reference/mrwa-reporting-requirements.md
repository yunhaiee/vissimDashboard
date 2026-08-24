# MRWA 보고 요구 발췌 — Report-requirement extracts (R-1 근거)

> 출처: Operational Modelling Guidelines – Version 2.0 (client-data/operational-modelling-v2.pdf).
> 전사 방법: RAG 코퍼스의 pypdf layout 추출 텍스트에서 축자 인용 (2026-08-24).
> 쪽 번호는 인쇄 쪽(printed page) 기준, PDF 쪽은 +18 오프셋 (#46의 유도 규칙).
> 이 파일은 Track R 스파인 모듈의 단일 근거이며, 코드의 인용 문자열은 이 파일과 핀으로 결박된다.

## §2.12.2 Model Validation — 보고서 구조 요구 (p.43)

축자 인용 (요구 문장만):

1. "Adjustments made to parameters during the model development process should be
   documented in the modelling report to provide a record of the changes." (p.43)
2. "In addition to this record, Main Roads requires a statistical comparison of model
   performance with on-street observations." (p.43)
3. "The modelling report should include separate sections for the reporting of calibration
   and validation and the modeller should state which of the observed datasets have been
   used for each process." (p.43)
4. "A comparison of each of the observed datasets against modelled outputs should then be
   presented in either the calibration or validation sections of the report." (p.43)
5. "The decision to accept or reject a model lies with Main Roads and modellers should
   explain any limitations in the modelling that might prevent the model from achieving
   the guideline calibration and validation criteria." (p.43)

파생 스파인 요구 (각 항목은 위 문장에서만 유도):

| 스파인 절 | 규정 근거 | 이 제품의 데이터 소스 |
|---|---|---|
| 파라미터 조정 기록 | 인용 1 | 승인 원장 (approval ledger — 누가·언제·왜·값) |
| 통계 비교 (모델 vs 실측) | 인용 2 | reality_check 리포트 (GEH 표) |
| 보정 절 / 검증 절 분리 + 데이터셋 공시 | 인용 3, 4 | 워크북/관측 세트 식별자 + 홀드아웃 분리 (#51의 split) |
| 한계 설명 | 인용 5 | 게이트 NOT_CHECKABLE/거부 소견 + 명명된 한계들 |

## §5.8 / §5.8.1 Model Output — 요구 산출물 (p.219)

축자 인용:

- "Modellers are required to report model assessment results to Main Roads for review when
  the base model is validated and when proposed option models are completed." (p.219)
- "Main Roads will require the model evaluation results to be reported as the final model
  outcomes in the modelling report. The model outputs include, but are not limited to those
  shown in Table 5-15. The results extracted from the model should be clearly presented in
  tabular or graphical forms." (p.219)

### Table 5-15: Model evaluation outputs (p.219, 전사)

| Type | Model output | Evaluation method | Time period |
|---|---|---|---|
| Network-wide | Number of vehicle served · Total travel distance · Total travel time · Average speed · Total delay · Average delay per vehicle · Total latent traffic demand | Vehicle network performance | Total analysis period (peak) |
| Intersection | Turning movement volume · Approach queue length · Average delay for each approach and intersection · LoS for each turning movement and intersection | Node · Queue counter | 1 hour interval analysis period |
| Route | Volume · Average travel time · Travel distance · Average delay time | Vehicle travel time · Delay | 1 hour interval analysis period |
| Critical section (for example, bottle neck) | Traffic volume · Density · LoS · Heat map · Vehicle speed | Link · Data collection | 15 minute interval analysis period |

### §5.8.1.1 / §5.8.1.2 (p.220)

- "Network-wide model performance outputs should be presented for the scoped evaluation
  time period with appropriate time intervals." (p.220) — 예시 표는 Table 5-16 (Base AM/PM
  2시나리오: vehicles arrived, total travel distance/time, total delay, average speed,
  average delay, demand latent).
- "The intersection performance outputs should include directional traffic volume, vehicle
  delay, level of service and queue length." (p.220)

## 정직 캐비앗 (전사 시점에 명명)

- Table 5-15는 "include, but are not limited to" — 최소 집합이지 전부가 아니다.
- Critical section 행의 Heat map·Density는 이 제품이 현재 추출하지 못하는 산출물이다 —
  스파인 모듈은 이를 NOT_AVAILABLE로 명명해야지, 절을 조용히 빼먹으면 안 된다.
- §2.12.2.1 모델 카테고리(1~3)는 검증 기준 선택에 영향을 주며 별도 전사 대상 (스파인
  v1은 카테고리 표기만, 기준 매핑은 기존 validation criteria 참조팩이 담당).
- p.43 좌우 병기·표는 layout 추출 특성상 공백 정규화됨 — 인용 대조 시 공백 비교는
  정규화 후에 (기존 참조팩 규율과 동일).
