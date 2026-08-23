# 보정 워크스페이스 독트린 — Calibration doctrine (단일 진실 공급원)

> **이 파일이 무엇인가.** `/calibration` 화면에서 퇴거된 독트린·가드·교육 문장의 단일
> 진실 공급원입니다 (#50-d, 사용자 확정 2026-08-20 — "화면은 사용자에게 행동을 빚졌지
> 교육을 빚진 게 아니다"). 문장은 삭제되지 않았고 이곳으로 이사했습니다.
>
> **이 파일을 읽는 세 독자.**
> 1. 프런트 계약 테스트(`frontend/tests/dashboard-contract.test.ts`)가 이 파일의 문장을
>    핀합니다 — 문장이 화면 표면에 다시 나타나면 죽고, 이 파일에서 사라져도 죽습니다.
> 2. 인앱 문서 페이지(`/docs`)가 같은 문장을 렌더합니다 (`frontend/lib/help-content.tsx`
>    의 `calibrationHelpEntries`). 두 사본의 불일치는 계약 테스트가 잡습니다.
> 3. RAG 인덱스가 이 파일을 수집해 어시스턴트가 검색·인용할 수 있습니다.
>
> **문장을 고치려면** 이 파일과 `help-content.tsx`를 함께 고치십시오. 화면 표면에 독트린
> 문장을 되돌리는 것은 #50-d의 사용자 결정을 뒤집는 일이므로 사용자 승인이 필요합니다.
>
> 표기: 각 항목은 ko/en/ja 축자 3언어. `{…}` 는 런타임 값이 들어가는 자리(서버 데이터)로,
> 화면에서는 값이 채워진 형태로만 존재했습니다.

---

## D1. 이 화면에 대하여 — What this screen is

- ko: MRWA §2.12.1의 13단계 순서대로, 이 워크스페이스가 실제로 가진 증거만으로 어디까지 왔는지 보여줍니다.
- en: Where this project stands in MRWA §2.12.1's thirteen steps, derived only from evidence this workspace actually holds.
- ja: MRWA §2.12.1の13段階の順に、このワークスペースが実際に持つ証拠だけで現在地を示します。

## D2. 상태 유도 규칙 — How a step's status is derived (the honest headline)

- ko: 각 단계의 상태는 이 워크스페이스에 실제로 있는 증거에서만 유도합니다. 증거가 없는 단계는 완료로 표시하지 않고 '증거 없음 — 수동 확인 영역'으로 둡니다.
- en: Each step's status is derived only from evidence this workspace actually holds. A step with no evidence is never shown as done — it stays a manual-check area.
- ja: 各段階の状態は、このワークスペースに実在する証拠からのみ導出します。証拠のない段階は完了とせず「証拠なし — 手動確認領域」とします。

## D3. 채점·진단은 MRWA 단계가 아니다 — Scoring & diagnosis is not a step

- ko: 채점·진단은 MRWA 단계 아님 — §2.12.1의 13단계에 속하지 않습니다. 실행을 실측과 대조해 어느 단계로 가야 할지 정하는 자리입니다.
- en: Scoring & diagnosis is not an MRWA step — it is not one of §2.12.1's thirteen. It is where the run is compared with observed counts to decide which step to go to.
- ja: 採点・診断はMRWA段階ではありません — §2.12.1の13段階には含まれません。実行を実測と対照し、どの段階へ進むかを決める場所です。

## D4. 수동 확인 영역이란 — What a manual-check area is

- ko: 수동 확인 영역 — 이 단계에 대해 이 제품은 아무것도 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오.
- en: Manual-check area — this product measures nothing for this step. Check it in Vissim and record it outside this tool.
- ja: 手動確認領域 — この段階について本製品は何も測定しません。Vissimで直接確認し、本ツールの外に記録してください。

## D5. 모델 값의 출처 규칙 — Where modelled values come from

- ko: 모델 값은 선택한 실행의 결과에서 가져옵니다. 워크북에 붙여넣은 모델 열은 다른 실행의 것일 수 있어 사용하지 않습니다.
- en: Modelled values come from the selected run's own results. The workbook's pasted model column is not used — it may describe a different run.
- ja: モデル値は選択した実行の結果から取得します。ワークブックに貼り付けられたモデル列は別の実行のものである可能性があるため使用しません。

## D6. 지도 읽기 규칙 — How to read the station map

- ko: 강조된 노드는 이 단계의 다음 행동이 가리키는 노드입니다. 좌표가 없는 노드는 지도에 나타나지 않습니다.
- en: The highlighted nodes are the ones this step's next action points at. A node without coordinates does not appear on the map.
- ja: 強調されたノードは、この段階の次の行動が指すノードです。座標のないノードは地図に表示されません。

## D7. 승인 경로는 하나 — One approval path

- ko: 승인 경로는 드로어와 같은 하나뿐입니다.
- en: There is exactly one approval path, shared with the drawer.
- ja: 承認経路はドロワーと同じ一つだけです。

## D8. 단계별 근거 규칙 — What each station's evidence is

### D8-a. 신호 (⑥·⑦) — Signals

- ko: ⑥의 근거는 사람이 서명한 현시도 확인서입니다 — 확인되지 않은 현시도는 신호가 검증됐다는 증거가 아닙니다. ⑦의 근거는 Table 5-6 적용 판정이며, 표 전체는 ⑫ 행태 스테이션에 있습니다.
- en: Step 6's evidence is a signed 현시도 (phase-diagram) attestation — an unattested diagram is not evidence that the signals were verified. Step 7's evidence is the Table 5-6 application verdict; the full table lives in the ⑫ behaviour station.
- ja: ⑥の根拠は人が署名した現示図の確認書です — 未確認の現示図は信号が検証された証拠ではありません。⑦の根拠はTable 5-6の適用判定で、表全体は⑫挙動ステーションにあります。

### D8-b. 행태 (⑦·⑫) — Behaviour

- ko: 각 주행행태의 현재 ax·bx_add·bx_mult가 공표된 표의 한 행 위에 있는지 확인합니다 (단계 ⑦·⑫).
- en: Whether each driving behaviour's current ax/bx_add/bx_mult sits on one published row of the table (steps 7 and 12).
- ja: 各走行挙動の現在のax・bx_add・bx_multが公表された表の一行にあるかを確認します（段階⑦・⑫）。

### D8-c. 대중교통 (⑨) — Public transport

- ko: 이 단계에 대해 이 제품이 가진 사실은 네트워크의 대중교통 노선 수 하나뿐입니다. 노선이 0개일 때만 '해당 없음'이 되고, 노선이 있으면 보정 여부를 알 방법이 없어 '증거 없음'으로 남습니다.
- en: The only fact this product holds for this step is the network's public-transport line count. Zero lines earns "not applicable"; with lines present there is no way to tell a calibrated network from an untouched one, so it stays "no evidence".
- ja: この段階について本製品が持つ事実は、ネットワークの公共交通路線数だけです。路線が0件のときのみ「該当なし」となり、路線がある場合は較正の有無を判断できないため「証拠なし」のままです。

### D8-d. 수요 (⑩) — Demand

- ko: 근거는 실행–실측 대조가 낸 수요 제안과 입구 교통량의 실측 확인(D4)입니다. D4가 말하는 것은 '미실측'이 아니라 '확인되지 않음'입니다.
- en: The evidence is the demand proposals the run-versus-observed check raised, plus the D4 entrance-measurement verdicts. D4 says "unverified", never "unmeasured".
- ja: 根拠は実行と実測の対照が出した需要提案と、入口交通量の実測確認(D4)です。D4が示すのは「未実測」ではなく「未確認」です。

### D8-e. 배분 (⑪) — Assignment

- ko: 근거는 실행–실측 대조가 낸 배분 제안과 ROUTING_SPLIT 진단입니다. 제안도 진단도 없다는 것은 배분이 확인됐다는 증거가 아닙니다.
- en: The evidence is the route-split proposals the run-versus-observed check raised and the ROUTING_SPLIT diagnoses. Neither a proposal nor a diagnosis is not evidence that route choice was confirmed.
- ja: 根拠は実行と実測の対照が出した配分提案とROUTING_SPLIT診断です。提案も診断もないことは、配分が確認された証拠ではありません。

### D8-f. 수동 확인 영역 — Manual-check steps

- ko: 위 단계들은 이 제품이 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오. 화면이 이 단계들을 완료로 표시하는 일은 없습니다.
- en: This product measures nothing for the steps above. Check them in Vissim and record them outside this tool — the screen will never show them as done.
- ja: 上記の段階について本製品は測定しません。Vissimで直接確認し、本ツールの外に記録してください。画面がこれらを完了と表示することはありません。

## D9. 수동 스테이션의 한 줄 판정 — The manual station's one-line verdict

이 한 줄은 화면 표면에도 남습니다(스테이션의 판정이자 약속이므로) — 이 파일에는 규칙의
원본으로 기록됩니다.

- ko: 이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다.
- en: This product measures nothing for the steps above — it never shows them as done.
- ja: 本製品は上記の段階を測定しません — 完了と表示することはありません。

## D10. W74 기본값 우연 일치 — The Vissim-default coincidence is not a measurement

화면에는 이 상태의 짧은 경고(개수 포함)만 남고, 이유는 여기에 있습니다. `{N}`·`{row}` 는
해당 네트워크의 실측 개수·행입니다.

- ko: 주행행태 {N}개가 공표 행과 일치하지만 정지 시 거리가 지침값(2.5 m)이 아닙니다. Vissim 기본값이 마침 {row} pcu 행과 같으므로, 이 일치는 측정이 제출됐다는 증거가 아닙니다.
- en: {N} behaviour(s) match a published row but do not carry the guideline standstill distance (2.5 m). Vissim's default happens to equal the {row} pcu row, so this match is not evidence that a measurement was submitted.
- ja: 走行挙動{N}件が公表行と一致しますが、停止時距離が指針値(2.5 m)ではありません。Vissimの既定値がちょうど{row} pcu行と同じため、この一致は測定が提出された証拠にはなりません。

## D11. 제안 배치의 정직 — Proposal placement never invents a step

### D11-a. 미매핑 도구 — Unmapped tools

- ko: 이 제안의 편집 도구는 §2.12.1의 어느 단계에도 매핑되어 있지 않아 여기(채점·진단)에 둡니다 — 단계를 지어내지 않습니다.
- en: This proposal's edit tools map to no §2.12.1 step, so it is shown here (scoring & diagnosis) — the step is not invented.
- ja: この提案の編集ツールは§2.12.1のいずれの段階にも対応付けられていないため、ここ（採点・診断）に表示します — 段階を作り出しません。

### D11-b. 스테이션 미해결 폴백 — Station-fallback placement

- ko: 이 제안은 단계 {step}에 해당하지만 그 단계의 스테이션을 찾지 못해 여기(채점·진단)에 둡니다.
- en: This proposal belongs to step {step}, whose station could not be resolved, so it is shown here (scoring & diagnosis).
- ja: この提案は段階{step}に属しますが、その段階のステーションを解決できなかったため、ここ（採点・診断）に表示します。

### D11-c. 다단계 제안 — Multi-step proposals

- ko: 이 제안은 다른 단계의 조치도 함께 포함합니다.
- en: This proposal also carries actions belonging to other steps.
- ja: この提案には他の段階の操作も含まれます。

## D12. 빈 상태의 의미 — What an empty proposal list means

화면에는 한 줄 사실("승인 대기 제안 없음" / "이 단계에는 … · 다른 단계 {N}건")만 남고,
설명은 여기에 있습니다.

### D12-a. 다른 단계에 대기 중일 때

- ko: 이 단계에는 승인을 기다리는 제안이 없습니다. 다른 단계에 {N}건이 대기 중입니다 — 왼쪽 레일의 파란 배지를 보세요.
- en: No proposal awaits approval at this step. {N} are waiting at other steps — see the blue badges on the left rail.
- ja: この段階では承認待ちの提案はありません。他の段階で{N}件が待機中です — 左レールの青いバッジをご覧ください。

### D12-b. 아무 데도 없을 때

- ko: 승인을 기다리는 제안이 없습니다. 제안은 어시스턴트가 만들며, 만들어지면 해당 단계에 나타납니다.
- en: No proposal is waiting for approval. Proposals are produced by the assistant, and appear at their own step when they are.
- ja: 承認待ちの提案はありません。提案はアシスタントが作成し、作成されると該当する段階に表示されます。

## D13. 기준 모델 검증 탭 — The base-model validation tab

- ko: AM/PM 기본 시나리오의 교통량, GEH, 통행시간 검증 결과를 확인합니다.
- en: Validate AM/PM base scenarios with traffic volume, GEH, and travel time checks.
- ja: AM/PM基本シナリオの交通量・GEH・旅行時間の検証結果を確認します。

## D14. 검증 항목 커버리지 카드 — The criteria-coverage card

- ko: 이 페이지가 실제로 평가한 항목과 평가할 수 없는 항목을 구분해 표시합니다.
- en: Which criteria this page actually evaluated, and which it cannot.
- ja: このページが実際に評価した項目と評価できない項目を区別して表示します。

## D15. Critical 교차로 기준이 별도인 이유 — Why the critical-intersection criterion is separate

`{profile}`·`{X}`·`{Y}` 는 선택된 검증 프로파일의 이름·GEH 상한·통과율입니다.
(MRWA §5.6.3.1, p.213)

- ko: 네트워크 전체 기준({profile})을 만족하더라도, 지정한 critical 교차로는 개별적으로 GEH < {X} 를 만족해야 합니다. 통과율 {Y}% 로는 이 조건이 충족되지 않습니다.
- en: Even when the network-wide {profile} criteria are met, each designated critical intersection must individually achieve GEH < {X}. The {Y}% pass rate does not satisfy this.
- ja: ネットワーク全体の基準（{profile}）を満たしていても、指定したcritical交差点は個別にGEH < {X} を満たす必要があります。通過率 {Y}% ではこの条件は満たされません。

## D16. 탐색 산출물의 가드레일 — The guardrail on a searched value (⑫)

#50-d의 사용자 결정으로 ⑫ 가드레일 섹션 UI(2경로 예보·결정 칩·mono 코드 바)는 화면에서
제거됐습니다 ("unnecessary developer side information"). **백엔드 강제는 불변입니다** —
거부와 grant는 이 문서가 아니라 코드가 집행합니다
(`backend/services/agent/parameter_search.py`, `searched_behaviour_broker.py`,
`backend/services/agent/gates.py`). 채택된 결정: DECISIONS.md 2026-08-20 — #51 탐색
산출물에도 가드레일 예외를 (a) 확장. 아래 세 항목이 그 기계의 사람 언어 설명입니다.

### D16-a. 두 경로 예보 — The two-path forecast

- ko: 탐색이 산출한 값이 모델에 닿는 길은 두 가지이고, 예보는 언제나 두 줄이었습니다. 직접 발행(direct_edit) — 플래너가 공유 행태 파라미터를 직접 쓰려 하면 공유 행태 가드레일이 이름을 명명하며 거부합니다 (constraint SHARED_BEHAVIOR_REFUSAL). 승인 경로(approved_set) — 완료된 탐색의 승자를 승인 카드로 승인하면 원장에서 grant가 재발급되어 1회 소모로 적용됩니다. 이 경로의 조건 코드는 RADIUS_NAMED_IN_FULL — 동의 반경을 전부 명명할 수 없으면 예외가 성립하지 않습니다 (ko 표기: '통과 (반경 전부 명명 시)'). 예보 상태는 3값입니다: 거부 예상 / 통과 예상 / 미지 — 조건 코드 GUARDRAIL_WILL_REFUSE · GUARDRAIL_CLEAR · GUARDRAIL_UNKNOWN.
- en: A searched value reaches the model by two roads, so the forecast was always two lines. Issued directly (direct_edit) — a planner writing a shared behaviour parameter is refused BY NAME by the shared-behaviour guardrail (constraint SHARED_BEHAVIOR_REFUSAL). Through the approval machinery (approved_set) — approving a finished search's winner on its approval card re-mints the grant from the ledger and spends it once. That road's condition code is RADIUS_NAMED_IN_FULL: the exception does not hold unless the consent radius can be named in full. The forecast has three states — expected to refuse, expected to pass, and not forecast — carried by GUARDRAIL_WILL_REFUSE, GUARDRAIL_CLEAR and GUARDRAIL_UNKNOWN.
- ja: 探索が算出した値がモデルに届く経路は二つあり、予報は常に二行でした。単独発行(direct_edit) — プランナーが共有挙動パラメータを直接書こうとすると、共有挙動ガードレールが名指しで拒否します (constraint SHARED_BEHAVIOR_REFUSAL)。承認経路(approved_set) — 完了した探索の勝者を承認カードで承認すると、台帳から grant が再発行され1回消費で適用されます。この経路の条件コードは RADIUS_NAMED_IN_FULL で、同意半径を完全に命名できなければ例外は成立しません。予報の状態は3値です: 拒否見込み / 通過見込み / 未予報 — 条件コードは GUARDRAIL_WILL_REFUSE · GUARDRAIL_CLEAR · GUARDRAIL_UNKNOWN。

### D16-b. 결정 칩 — The resolution chips

- ko: 가드레일 충돌의 해소는 사용자 결정이며, 각 해소안의 상태(REJECTED_BY_USER 등)는 서버가 구동합니다. 문장과 상태 단어는 전부 서버의 것이었고, 지금도 서버 페이로드에 그대로 있습니다 — 화면이 렌더하지 않을 뿐입니다.
- en: Resolving a guardrail collision is the USER's decision, and each resolution's status (REJECTED_BY_USER and its kin) is driven by the server. Every sentence and every status word was the server's, and every one of them is still in the server payload — the screen simply no longer renders them.
- ja: ガードレール衝突の解消はユーザーの決定であり、各解消案の状態(REJECTED_BY_USER など)はサーバーが駆動します。文章も状態語もすべてサーバーのものであり、今もサーバーのペイロードにそのまま存在します — 画面が描画しないだけです。

### D16-c. 증거 등급 — Evidence rank

- ko: 탐색 산출물은 Table 5-6 산출물보다 한 단계 낮은 증거입니다 — 근거문(원장 digest·홀드아웃 seed 탑재)이 스스로 명시합니다. 이 문장은 서버가 합성하며, 승인 카드 표면에 계속 표시됩니다 — 승인 결정에 필요한 정직성이므로 #50-d에서도 퇴거되지 않았습니다.
- en: A searched value is evidence one rank below a Table 5-6 value, and the backend's own sentence — carrying the ledger digest and the holdout seed — says so. The server composes it, and it still renders on the surface of the approval card: it is honesty the approval decision needs, so #50-d did not evict it.
- ja: 探索産出物は Table 5-6 の産出物より一段低い証拠であり、根拠文（台帳の digest・ホールドアウトの seed を含む）が自ら明示します。この文章はサーバーが合成し、承認カードの表面に表示され続けます — 承認判断に必要な誠実さであるため、#50-d でも撤去されていません。

## D17. 지도의 좌표 출처 — Where node placement comes from (the scoring map)

#50-d 빌드 중 발견된 스펙 목록 밖 잔존 ⓘ (page.tsx 채점 지도, "지도에 대하여") —
D6과 겹치되 축자가 아니어서 별도 항목으로 등재하고 함께 퇴거합니다.

- ko: 노드 위치는 데이터셋의 좌표 정보에서 옵니다. 좌표가 없는 노드는 지도에 나타나지 않으며, 왼쪽 타일이 전체 목록입니다.
- en: Node placement comes from the dataset's georeference. A node without coordinates does not appear on the map — the tiles beside it are the complete list.
- ja: ノード位置はデータセットの座標情報に基づきます。座標のないノードは地図に表示されません。左のタイルが完全な一覧です。

## D18. 사람 증언은 완료가 아니다 — A human attestation is not Done

#53(2026-08-24)이 추가한 상태어의 규칙: 증언은 증거의 한 형태이지 측정이 아니다.

- ko: 확인됨(사람 증언)은 완료가 아닙니다 — 사람이 서명한 단계이지 이 제품이 측정한 단계가 아니며, 서명한 네트워크가 바뀌면 다시 확인해야 합니다.
- en: Confirmed (human attestation) is not Done — it means a named person signed for the step, not that this product measured it, and it must be made again once the network they signed against changes.
- ja: 確認済み（人の証言）は完了ではありません — 人が署名した段階であり、本製品が測定した段階ではありません。署名した時点のネットワークが変われば、改めて確認が必要です。
