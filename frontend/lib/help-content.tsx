/**
 * THE DOCTRINE TEXT, IN ONE PLACE (2026-08-14).
 *
 * Unit #20 gave each genesis screen a round `?` beside its own h1, opening a
 * right-side drawer of static doctrine sections. The trigger is retired: four
 * screens each carrying their own reference surface meant the background of the
 * product was only reachable from the exact page it described, and only by a
 * reader who already knew to look for a small circle beside a heading. The
 * TEXT is not retired -- it moved here, verbatim, and renders as one reading
 * surface at /docs, reachable from the book icon in the app topbar on every
 * route.
 *
 * WHAT THIS FILE IS. Builders, not constants: every sentence is a `t()` call,
 * so the entries have to be built AFTER the reader's language is known. Each
 * one takes the `t` from `useLanguage()` and returns plain `{ title, body }`
 * sections in reading order -- the same shape `PageHelp` took, so the move was
 * a cut and a paste rather than a rewrite.
 *
 * WHAT IT MUST NOT BECOME. A place where doctrine is edited to sound better.
 * Several of these sentences are pinned by frontend/tests/dashboard-contract.
 * test.ts as the product's own claims about what it refuses to do (the region
 * boundary rule, the 현시도 human-confirmation rule, the R1.5 gate's "a check
 * that could not run is never a pass"). They are the same words the screens
 * shipped with; a change here is a change to what the product promises, not a
 * copy edit.
 *
 * STATIC ONLY, for the same reason `PageHelp` was: nothing here fetches, and no
 * entry states a measurement. A page that wants to show something LIVE (a job's
 * own verdict, a measured area) has that on the page itself. `regionHelpEntries`
 * takes `maxVertices` as its one exception and even that is not a measurement --
 * it is the server's declared corner cap, which the region screen happens to
 * know and /docs does not, so /docs renders the same 64 the region screen falls
 * back to when the server has not said.
 */

export type HelpEntry = { title: string; body: string };

/** The `t` from `useLanguage()`. ja is optional there, so it is optional here. */
export type Translate = (ko: string, en: string, ja?: string) => string;

/**
 * /genesis -- the hub. Trimmed by unit #23: 판정 게이트/잠정 등록/지도 describe
 * a generation's OWN detail page and live in `jobHelpEntries` below. What is
 * left is doctrine about the hub's own surface -- the form and the list.
 */
export function genesisHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("생성 과정", "How generation runs", "生成の流れ"),
      body: t(
        "생성은 항상 잡 큐를 통해 실행됩니다 — 이 화면이 Vissim COM을 직접 여는 일은 없습니다. 생성을 시작하려면 로그인해야 합니다.",
        "A generation always runs through the job queue — this screen never opens Vissim COM directly. Starting a generation requires a signed-in user.",
        "生成は常にジョブキューを通じて実行されます — この画面が Vissim COM を直接開くことはありません。生成の開始にはログインが必要です。",
      ),
    },
    {
      title: t("수요·신호 입력", "Demand & signals input", "需要・信号入力"),
      body: t(
        "두 목록은 이미 스테이징된 입력 중에서 고르기만 합니다 — 이 화면에서 만들지 않습니다. 옆의 '만들기' 링크가 그 입력을 실제로 만드는 화면으로 이동합니다.",
        "Both dropdowns only PICK from an already-staged input — nothing is built here. The '만들기' link beside each one goes to the screen that actually builds it.",
        "両方の一覧は、すでにステージング済みの入力から選ぶだけです — この画面では作成しません。隣の「作成」リンクが、実際に作成する画面へ移動します。",
      ),
    },
    {
      title: t("제외 도로", "Excluded roads", "除外道路"),
      body: t(
        "제외된 도로는 버려지지 않고 이름이 남습니다 — 준수 리포트에 사유와 함께 기록됩니다.",
        "Excluded ways are never dropped silently — each is named with its reason in the compliance report.",
        "除外された道路は黙って捨てられず、準拠レポートに理由とともに記録されます。",
      ),
    },
  ];
}

/**
 * /genesis/region -- the lasso extract.
 *
 * `maxVertices` is the ONE runtime value any of these builders takes, and it is
 * optional on purpose. The region screen reads the cap from the server's own
 * limits (`limits.max_polygon_vertices`) and holds it as `number | null`; /docs
 * has no server limits call of its own and passes nothing, which lands on the
 * SAME `?? 64` fallback the region screen used before its limits arrived. The
 * browser still carries no authoritative copy of the cap -- 64 is a
 * we-have-not-been-told placeholder, and the server's refusal is what actually
 * enforces it.
 */
export function regionHelpEntries(t: Translate, maxVertices?: number | null): HelpEntry[] {
  return [
    {
      title: t("경계 처리", "Boundary rule", "境界の扱い"),
      body: t(
        "그린 폴리곤이 그대로 기준입니다. Overpass 질의도, 도로 꼬리 잘라내기도, 모형 경계 끝점 판정도 모두 이 폴리곤으로 합니다 — 폴리곤 밖으로 뻗은 도로는 폴리곤 변에서 자르고 그 자리에 노드를 만듭니다. 옅은 점선 사각형은 폴리곤의 바운딩 박스로, 거리 기반 경계 띠와 용량 추정에만 쓰입니다. 이 내용은 생성된 준수 보고서에도 그대로 적힙니다.",
        "The polygon you drew is the boundary. Overpass is queried with it, the way-tail trim cuts at it, and a road end is judged against it — a road running past the polygon is cut AT its edge and a node is placed there. The faint dashed rectangle is the polygon's bounding box, used only for the distance-based boundary band and the size estimate. The generated compliance report states this too.",
        "描いたポリゴンがそのまま基準です。Overpass の問い合わせも、道路の尾部の切り詰めも、モデル境界の端点判定も、すべてこのポリゴンで行います — ポリゴンの外へ伸びる道路はその辺で切り詰め、そこにノードを作ります。薄い破線の矩形はポリゴンのバウンディングボックスで、距離ベースの境界帯と規模の見積りにのみ使います。この内容は生成される準拠レポートにも記載されます。",
      ),
    },
    {
      title: t("넓이 한계", "Area limit", "面積の上限"),
      body: t(
        "한계는 생성기의 투영이 1 m 오차 예산에 도달하는 반경입니다 — 이 선을 넘으면 생성기가 어차피 projection_out_of_range로 거부하므로, 이 화면은 내려받기 전에 거부합니다(잘라내지 않습니다). 정사각형이라면 한계에 대응하는 최대 넓이가 있지만, 길쭉한 영역은 넓이가 작아도 반경에서 걸릴 수 있습니다. '투영 오차'는 위경도를 평면 좌표로 바꿀 때 이 반경에서 생기는 최대 평면 오차입니다.",
        "The limit is the half-extent at which the generator's projection reaches its 1 m error budget. Past that line the generator refuses outright (projection_out_of_range), so this screen refuses before the download rather than truncating it. A square region has a maximum area at that limit, but a long thin region can trip the half-extent even at a small area. 'Projection error' is the largest planar error the WGS84 conversion produces at that radius.",
        "上限は、生成器の投影が 1 m の誤差予算に達する半径です。この線を越えると生成器がいずれにせよ projection_out_of_range で拒否するため、この画面は取得前に拒否します（切り詰めません）。正方形ならその上限に対応する最大面積がありますが、細長い領域は面積が小さくても半径で引っかかることがあります。「投影誤差」は、その半径で WGS84 からの変換により生じる最大の平面誤差です。",
      ),
    },
    {
      title: t("라쏘 조작", "Using the lasso", "投げ縄の操作"),
      body: t(
        `지도를 눌러 점을 찍고, 두 번 눌러 영역을 닫습니다. 점은 최대 ${maxVertices ?? 64}개까지 찍을 수 있습니다 — 자동으로 줄이지 않으니 모양은 단순하게 그려 주세요. '초기화'를 누르면 모든 점이 지워집니다.`,
        `Click to add a point, double-click to close the shape. At most ${maxVertices ?? 64} corners can be added — draw with fewer, since it is never simplified for you. 'Reset' clears every point.`,
        `地図をクリックして点を打ち、ダブルクリックで領域を閉じます。点は最大 ${maxVertices ?? 64} 個まで打てます — 自動では減らさないため、単純な形で描いてください。「初期化」を押すとすべての点が消えます。`,
      ),
    },
    {
      title: t("데이터 출처", "Data source", "データの出所"),
      body: t(
        "지도 데이터는 OpenStreetMap에서 받아오며, OSM의 ODbL 라이선스를 따릅니다. 영역을 추출하려면 로그인이 필요합니다.",
        "Map data is fetched from OpenStreetMap under its ODbL licence. Extracting a region requires a signed-in user.",
        "地図データは OpenStreetMap から取得し、OSM の ODbL ライセンスに従います。領域の抽出にはログインが必要です。",
      ),
    },
    {
      title: t("추출 이후", "After the extract", "抽出のあと"),
      body: t(
        "받은 추출은 업로드한 파일과 똑같은 경로로 생성·검증 게이트를 거칩니다 — 이 화면은 스테이징 문 앞에서 끝나고, 생성을 직접 시작하지 않습니다.",
        "The extract takes exactly the same road as an uploaded file through generation and the validation gate — this screen stops at the staging door and never starts a generation itself.",
        "取得した抽出はアップロードしたファイルとまったく同じ経路で生成・検証ゲートを通ります — この画面はステージングの入口で終わり、自ら生成を開始することはありません。",
      ),
    },
  ];
}

/**
 * /genesis/phases -- the 현시도 review. Re-cut by unit #55
 * (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀 업로드로
 * 통합, 판독 확인 클릭은 제거 (#55)"): an uploaded workbook is no longer a proposal
 * awaiting a per-intersection click -- it IS the uploader's own statement of the
 * reading, the moment it lands. What stays a human act is the BINDING of that
 * reading's approaches to a generated draft's node and link ids, reached from
 * each card's 네트워크 결박 section at signal-build time. `결박이 사람의 몫입니다`
 * restates that split; it is the doctrine the page's own always-visible intro
 * paragraph carries too (that paragraph stays ON the page -- it is that
 * screen's foundational claim, not a tooltip), and this is the copy a reader
 * finds when they come to the documentation instead.
 */
export function phasesHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("결박이 사람의 몫입니다", "Binding stays a human act", "結縛は人の仕事です"),
      body: t(
        "현시도는 그림이므로 어떤 파싱도 접근로–현시 배정을 자동으로 만들어내지 못합니다. 템플릿을 채워 올린 엑셀은 올린 사람 자신의 판독 기록이며, 이 화면은 그 기록을 그대로 도식으로 그릴 뿐 정오를 판단하지 않습니다. 초안 네트워크의 어느 노드·링크에 묶이는지만은 signals 입력을 만들 때 사람이 각 카드의 네트워크 결박 절에서 정하며, 그 결박이 이 제품에서 사람 손으로 남아 있는 유일한 확인입니다.",
        "The 현시도 is a drawing, so no parse can produce the approach→phase mapping on its own. A workbook somebody filled in and uploaded is THAT PERSON'S OWN reading; this screen draws it exactly as written and judges nothing. Only which draft node and link ids it binds to is still decided by a human, in each card's 네트워크 결박 section, at the moment a signals input is built -- that binding is the one human-confirmed act left in this product.",
        "現示図は図面であり、解析だけでは流入路と現示の対応を自動的には作れません。テンプレートを記入して送られたエクセルは提出者自身の判読記録であり、この画面はそれをそのまま図として描くだけで正誤は判断しません。草案ネットワークのどのノード・リンクに結びつくかのみ、signals入力を作るときに人がカードの네트워크 결박で決めます — その結縛が、この製品に残る唯一の人による確認です。",
      ),
    },
    {
      title: t("signals 입력", "Signals input", "信号入力"),
      body: t(
        "네트워크 결박까지 확인된(CONFIRMED) 교차로만 signals 입력에 포함됩니다 — 업로드만 되고 결박이 없거나 재확인이 필요한 교차로는 빠집니다.",
        "Only intersections CONFIRMED all the way to their network binding are included in the signals input — an upload with no binding yet, or one needing re-confirmation, is left out.",
        "signals入力には、ネットワーク結縛まで確認済み（CONFIRMED）の交差点のみが含まれます — アップロードのみで結縛がない、または再確認が必要な交差点は除外されます。",
      ),
    },
    // Unit #52 — three ways in, one status; re-worded by #55 for what that
    // status now IS. The screen itself carries the three buttons; the reason
    // they all land on the same store lives here.
    {
      title: t("입력하는 세 가지 방법", "Three ways to add one", "入力する三つの方法"),
      body: t(
        "템플릿을 내려받아 채운 뒤 올리거나, 화면에서 빈 양식을 새로 만들거나, 이미 있는 기록을 엑셀 초안으로 내보내 고친 뒤 다시 올릴 수 있습니다. 세 가지 모두 결과는 올린 사람의 기록이며, 그 기록이 어느 초안 노드·링크에 묶이는지는 이후 네트워크 결박에서 사람이 정합니다.",
        "Download the template and upload it filled in, create a blank form on the screen, or export an existing record as a draft workbook, correct it and send it back. All three produce THAT PERSON'S OWN RECORD -- which draft node and links it binds to is decided afterward, by a human, in 네트워크 결박.",
        "テンプレートを記入して送るか、画面で空の様式を作るか、既存の記録を草案として書き出し修正して再送します。三つとも結果は送った本人自身の記録であり、どの草案ノード・リンクに結びつくかはその後、人が네트워크 결박で決めます。",
      ),
    },
    {
      title: t("엑셀에는 도면이 없습니다", "A spreadsheet carries no drawing", "エクセルに図面はありません"),
      body: t(
        "엑셀로 만든 검토와 직접 만든 빈 양식에는 현시도 그림이 없습니다. 이 앱은 그 자리에 가짜 이미지를 넣지 않고 '이미지 없음'이라고 적습니다 — 결박 확인 기록은 사람이 실제로 본 그림의 해시에 묶이므로, 없는 그림을 있는 것처럼 표시하면 그 기록이 거짓이 됩니다. 또한 한 줄이라도 거부되면 그 교차로 전체를 가져오지 않습니다.",
        "A review made from a spreadsheet, or created blank on the screen, has no 현시도 image. This app writes '이미지 없음' there instead of a stand-in picture: the binding attestation is bound to the hash of the drawing the person actually looked at, so a fake image would make that record a lie. And if one row is refused, the whole intersection is left out rather than half-imported.",
        "エクセルから作成した検討や画面で作成した空の様式には現示図の画像がありません。本アプリは代わりの画像を置かず「画像なし」と記します — 結縛確認の記録は人が実際に見た図面のハッシュに紐づくためです。また一行でも拒否されればその交差点全体を取り込みません。",
      ),
    },
    // #55's new drawn strip is not a scan substitute -- it is a redrawing of
    // the parsed rows, and it inherits the template's own missing-bearing
    // limit. See components/genesis/PhaseDiagramSketch.tsx for the rule as
    // code; this is the same claim as prose, for the reader who never opens
    // the source.
    {
      title: t("그려진 도식은 배치이지 실제 지리가 아닙니다", "The drawn strip is a layout, not real geography", "描かれた図は配置であり実際の地理ではありません"),
      body: t(
        "업로드나 빈 양식에는 현시도 그림이 없으므로, 이 화면은 파싱된 접근로·현시 값으로 도식을 새로 그립니다. 이 도식은 방위 열이 없는 템플릿(v1)의 한계를 그대로 물려받아, 접근로의 위치를 시트에 적힌 순서로만 배치합니다 — 실제 방위를 주장하지 않으며, 판독되지 않은(미상) 이동류는 화살표가 아니라 글로 적힌 칩으로만 나타납니다.",
        "An upload or a blank form carries no 현시도 image, so this screen draws its own diagram from the parsed approach and phase values. That drawing inherits the v1 template's own limit -- no bearing column -- and places each leg only in the order the sheet lists it; it makes no claim about a real compass direction, and any movement the sheet left unresolved (미상) renders as a text chip, never as an arrow.",
        "アップロードや空の様式には現示図の画像がないため、この画面は解析された流入路・現示の値から独自に図を描きます。この図はv1テンプレート自体の制約(方位の列がない)をそのまま引き継ぎ、各流入路をシートに記載された順序のみで配置します — 実際の方位を主張せず、判読できなかった(不明な)移動流は矢印ではなく文字のチップとしてのみ表示されます。",
      ),
    },
  ];
}

/**
 * /observations -- the observation STORE (unit #28). What a set is FOR: it
 * feeds `run_reality_check`'s `observation_set_id` parameter as the OBSERVED
 * side (unit U10), so the doctrine here is about that later comparison, not
 * about the upload form itself (the form's own preconditions render inline,
 * next to the 적재 button they gate -- doctrine belongs here, a disabled-reason
 * does not).
 */
export function observationsHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("실측 관측이란", "What an observation set is", "実測観測とは"),
      body: t(
        "현장에서 잰 교통량 워크북을 게이트에 통과시켜 만든, 기간별·지점별 합계입니다. 이후 실행 결과를 이 세트와 대조할 때 '실측' 쪽 숫자가 됩니다 — 개별 레코드가 아니라 합계만 보관합니다.",
        "The period-by-period, site-by-site totals a fieldwork counts workbook produces once it clears the data-quality gate. It becomes the 'observed' side when a later run is graded against it -- only the AGGREGATES are kept, never the individual records.",
        "現場で計測した交通量ワークブックがデータ品質ゲートを通過してできる、期間別・地点別の合計です。以降の実行結果をこのセットと対照する際の「実測」側になります — 個々のレコードではなく合計のみを保管します。",
      ),
    },
    {
      title: t("접근로|회전 키", "The leg|turn key", "流入路|旋回キー"),
      body: t(
        "지점별 집계는 '접근로|회전' 형태의 이동류 키로 나뉩니다 — 접근로명은 조사 원문 그대로이고, 회전은 인식되면 좌회전/직진/우회전/유턴, 아니면 원문 토큰입니다. 이 키가 어느 Vissim 노드·이동류에 해당하는지는 대조를 실행하는 쪽이 알아야 하는 정보이며, 이 화면은 그 매핑을 만들지 않습니다.",
        "A site's aggregate is split by a 'leg|turn' movement key -- the leg is the survey's own approach label verbatim, and the turn is left/through/right/u-turn when recognised, or the survey's own token otherwise. Which Vissim node and movement that key corresponds to is knowledge the comparison itself needs; this screen never builds that mapping.",
        "地点別集計は「流入路|旋回」の形式の移動流キーで分かれます — 流入路名は調査原文そのままで、旋回は認識できれば左折/直進/右折/Uターン、できなければ原文のトークンです。このキーがどのVissimノード・移動流に対応するかは対照を実行する側が知るべき情報であり、この画面はその対応付けを作成しません。",
      ),
    },
    {
      title: t("시간대는 반드시 선언", "Periods are always declared", "時間帯は必ず宣言"),
      body: t(
        "몇 주치 조사를 한 덩어리로 합산해 짧은 실행과 비교하면 없는 수요 오차를 만들어 냅니다. 그래서 적재할 때 최소 하나의 시간대 이름·시작·끝을 반드시 선언해야 하며, 이 화면이 추측하는 일은 없습니다.",
        "Summing a multi-week survey into one figure and comparing it to a short run would manufacture a demand error that is not real. So storing a set always requires at least one named period (name, start, end) declared up front -- this screen never infers one.",
        "数週間分の調査を一括で合算して短い実行と比較すると、実在しない需要誤差を作り出します。そのため取り込み時には最低一つの時間帯（名前・開始・終了）を必ず宣言する必要があり、この画面が推測することはありません。",
      ),
    },
    {
      title: t("거부되면 아무것도 저장되지 않음", "A refusal stores nothing", "拒否されると何も保存されない"),
      body: t(
        "데이터 품질 게이트가 거부 등급 결함을 발견하면 그 세트는 저장되지 않고, 거부 사유가 그대로 화면에 표시됩니다. 거부된 데이터를 저장하면 이후 대조가 잘못된 숫자를 근거로 삼게 되기 때문입니다.",
        "When the data-quality gate finds a refuse-tier fault, the set is not stored at all and the refusal reasons render exactly as the gate reported them. Storing refused data would let a later comparison grade a run against numbers already known to be wrong.",
        "データ品質ゲートが拒否ティアの欠陥を発見すると、そのセットは保存されず、拒否理由がゲートの報告どおりにそのまま表示されます。拒否されたデータを保存すると、以降の対照が誤りだと分かっている数値を根拠にしてしまうためです。",
      ),
    },
  ];
}

/**
 * /calibration -- the calibration workspace's DOCTRINE, evicted from the screen
 * (#50-d, user-confirmed 2026-08-20).
 *
 * WHY IT IS HERE. Driving #50-c's real screen the user's decision was that a
 * calibration screen owes the reader an ACTION, not an education: "얘넨 다 숨겨".
 * Seven ⓘ tooltips, six per-station folded rules and a developer-grade guardrail
 * section were background a reader needs once and was made to step over on every
 * visit. NOTHING IS DELETED. Every sentence below is the sentence that used to
 * render on that screen, verbatim, and it now has exactly one home per reader:
 * this module renders it at /docs, docs/product/calibration-doctrine.md carries
 * the same text for the contract suite and the assistant's RAG corpus, and
 * frontend/tests/dashboard-contract.test.ts pins all three copies together --
 * so a sentence cannot be softened here without the same edit landing there.
 *
 * WHAT STAYED ON THE SCREEN, and it is a short list: verdicts and actions. The
 * per-step basis line, every status chip, the locked ⑫ track's named refusals,
 * the 수동 확인 영역 station's one-line promise (D9 below, which is here as the
 * RULE's original and there as the station's verdict), the searched value's
 * evidence-rank sentence, and the count of proposals waiting elsewhere. A
 * sentence that tells the reader what the product WILL NOT DO is a verdict; a
 * sentence that teaches them how the product works is this file's.
 *
 * `{N}`, `{step}`, `{profile}`, `{X}`, `{Y}` are the runtime values the screen
 * filled in. They are written here as placeholders because this module is
 * STATIC and states no measurement -- the same rule the entries above obey.
 */
export function calibrationHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("이 화면에 대하여", "What this screen is", "この画面について"),
      body: t(
        "MRWA §2.12.1의 13단계 순서대로, 이 워크스페이스가 실제로 가진 증거만으로 어디까지 왔는지 보여줍니다.",
        "Where this project stands in MRWA §2.12.1's thirteen steps, derived only from evidence this workspace actually holds.",
        "MRWA §2.12.1の13段階の順に、このワークスペースが実際に持つ証拠だけで現在地を示します。",
      ),
    },
    {
      title: t("상태 유도 규칙", "How a step's status is derived", "状態の導出規則"),
      body: t(
        "각 단계의 상태는 이 워크스페이스에 실제로 있는 증거에서만 유도합니다. 증거가 없는 단계는 완료로 표시하지 않고 '증거 없음 — 수동 확인 영역'으로 둡니다.",
        "Each step's status is derived only from evidence this workspace actually holds. A step with no evidence is never shown as done — it stays a manual-check area.",
        "各段階の状態は、このワークスペースに実在する証拠からのみ導出します。証拠のない段階は完了とせず「証拠なし — 手動確認領域」とします。",
      ),
    },
    {
      title: t("채점·진단은 MRWA 단계가 아니다", "Scoring & diagnosis is not a step", "採点・診断はMRWA段階ではない"),
      body: t(
        "채점·진단은 MRWA 단계 아님 — §2.12.1의 13단계에 속하지 않습니다. 실행을 실측과 대조해 어느 단계로 가야 할지 정하는 자리입니다.",
        "Scoring & diagnosis is not an MRWA step — it is not one of §2.12.1's thirteen. It is where the run is compared with observed counts to decide which step to go to.",
        "採点・診断はMRWA段階ではありません — §2.12.1の13段階には含まれません。実行を実測と対照し、どの段階へ進むかを決める場所です。",
      ),
    },
    {
      title: t("수동 확인 영역이란", "What a manual-check area is", "手動確認領域とは"),
      body: t(
        "수동 확인 영역 — 이 단계에 대해 이 제품은 아무것도 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오.",
        "Manual-check area — this product measures nothing for this step. Check it in Vissim and record it outside this tool.",
        "手動確認領域 — この段階について本製品は何も測定しません。Vissimで直接確認し、本ツールの外に記録してください。",
      ),
    },
    {
      title: t("모델 값의 출처 규칙", "Where modelled values come from", "モデル値の出典規則"),
      body: t(
        "모델 값은 선택한 실행의 결과에서 가져옵니다. 워크북에 붙여넣은 모델 열은 다른 실행의 것일 수 있어 사용하지 않습니다.",
        "Modelled values come from the selected run's own results. The workbook's pasted model column is not used — it may describe a different run.",
        "モデル値は選択した実行の結果から取得します。ワークブックに貼り付けられたモデル列は別の実行のものである可能性があるため使用しません。",
      ),
    },
    {
      title: t("지도 읽기 규칙", "How to read the station map", "地図の読み方"),
      body: t(
        "강조된 노드는 이 단계의 다음 행동이 가리키는 노드입니다. 좌표가 없는 노드는 지도에 나타나지 않습니다.",
        "The highlighted nodes are the ones this step's next action points at. A node without coordinates does not appear on the map.",
        "強調されたノードは、この段階の次の行動が指すノードです。座標のないノードは地図に表示されません。",
      ),
    },
    // D17 sits beside D6 rather than at the end: they are the two rules about
    // the same object. D6 answers "which nodes are highlighted", D17 answers
    // "why is a node missing from the map" — and a reader who has just asked one
    // is about to ask the other. It was found during #50-d's own build, on the
    // scoring tab's map caption, outside the eviction list; the knowledge file
    // registered it as its own item because it overlaps D6 without being
    // verbatim, and it was evicted with the rest.
    {
      title: t("지도의 좌표 출처", "Where node placement comes from", "地図の座標の出所"),
      body: t(
        "노드 위치는 데이터셋의 좌표 정보에서 옵니다. 좌표가 없는 노드는 지도에 나타나지 않으며, 왼쪽 타일이 전체 목록입니다.",
        "Node placement comes from the dataset's georeference. A node without coordinates does not appear on the map — the tiles beside it are the complete list.",
        "ノード位置はデータセットの座標情報に基づきます。座標のないノードは地図に表示されません。左のタイルが完全な一覧です。",
      ),
    },
    {
      title: t("승인 경로는 하나", "One approval path", "承認経路は一つ"),
      body: t(
        "승인 경로는 드로어와 같은 하나뿐입니다.",
        "There is exactly one approval path, shared with the drawer.",
        "承認経路はドロワーと同じ一つだけです。",
      ),
    },
    {
      title: t("단계별 근거 — 신호 (⑥·⑦)", "What each station's evidence is — signals", "段階別の根拠 — 信号 (⑥・⑦)"),
      body: t(
        "⑥의 근거는 사람이 서명한 현시도 확인서입니다 — 확인되지 않은 현시도는 신호가 검증됐다는 증거가 아닙니다. ⑦의 근거는 Table 5-6 적용 판정이며, 표 전체는 ⑫ 행태 스테이션에 있습니다.",
        "Step 6's evidence is a signed 현시도 (phase-diagram) attestation — an unattested diagram is not evidence that the signals were verified. Step 7's evidence is the Table 5-6 application verdict; the full table lives in the ⑫ behaviour station.",
        "⑥の根拠は人が署名した現示図の確認書です — 未確認の現示図は信号が検証された証拠ではありません。⑦の根拠はTable 5-6の適用判定で、表全体は⑫挙動ステーションにあります。",
      ),
    },
    {
      title: t("단계별 근거 — 행태 (⑦·⑫)", "What each station's evidence is — behaviour", "段階別の根拠 — 挙動 (⑦・⑫)"),
      body: t(
        "각 주행행태의 현재 ax·bx_add·bx_mult가 공표된 표의 한 행 위에 있는지 확인합니다 (단계 ⑦·⑫).",
        "Whether each driving behaviour's current ax/bx_add/bx_mult sits on one published row of the table (steps 7 and 12).",
        "各走行挙動の現在のax・bx_add・bx_multが公表された表の一行にあるかを確認します（段階⑦・⑫）。",
      ),
    },
    {
      title: t("단계별 근거 — 대중교통 (⑨)", "What each station's evidence is — public transport", "段階別の根拠 — 公共交通 (⑨)"),
      body: t(
        "이 단계에 대해 이 제품이 가진 사실은 네트워크의 대중교통 노선 수 하나뿐입니다. 노선이 0개일 때만 '해당 없음'이 되고, 노선이 있으면 보정 여부를 알 방법이 없어 '증거 없음'으로 남습니다.",
        'The only fact this product holds for this step is the network\'s public-transport line count. Zero lines earns "not applicable"; with lines present there is no way to tell a calibrated network from an untouched one, so it stays "no evidence".',
        "この段階について本製品が持つ事実は、ネットワークの公共交通路線数だけです。路線が0件のときのみ「該当なし」となり、路線がある場合は較正の有無を判断できないため「証拠なし」のままです。",
      ),
    },
    {
      title: t("단계별 근거 — 수요 (⑩)", "What each station's evidence is — demand", "段階別の根拠 — 需要 (⑩)"),
      body: t(
        "근거는 실행–실측 대조가 낸 수요 제안과 입구 교통량의 실측 확인(D4)입니다. D4가 말하는 것은 '미실측'이 아니라 '확인되지 않음'입니다.",
        'The evidence is the demand proposals the run-versus-observed check raised, plus the D4 entrance-measurement verdicts. D4 says "unverified", never "unmeasured".',
        "根拠は実行と実測の対照が出した需要提案と、入口交通量の実測確認(D4)です。D4が示すのは「未実測」ではなく「未確認」です。",
      ),
    },
    {
      title: t("단계별 근거 — 배분 (⑪)", "What each station's evidence is — assignment", "段階別の根拠 — 配分 (⑪)"),
      body: t(
        "근거는 실행–실측 대조가 낸 배분 제안과 ROUTING_SPLIT 진단입니다. 제안도 진단도 없다는 것은 배분이 확인됐다는 증거가 아닙니다.",
        "The evidence is the route-split proposals the run-versus-observed check raised and the ROUTING_SPLIT diagnoses. Neither a proposal nor a diagnosis is not evidence that route choice was confirmed.",
        "根拠は実行と実測の対照が出した配分提案とROUTING_SPLIT診断です。提案も診断もないことは、配分が確認された証拠ではありません。",
      ),
    },
    {
      title: t("단계별 근거 — 수동 확인 영역", "What each station's evidence is — manual-check steps", "段階別の根拠 — 手動確認領域"),
      body: t(
        "위 단계들은 이 제품이 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오. 화면이 이 단계들을 완료로 표시하는 일은 없습니다.",
        "This product measures nothing for the steps above. Check them in Vissim and record them outside this tool — the screen will never show them as done.",
        "上記の段階について本製品は測定しません。Vissimで直接確認し、本ツールの外に記録してください。画面がこれらを完了と表示することはありません。",
      ),
    },
    {
      title: t("수동 스테이션의 한 줄 판정", "The manual station's one-line verdict", "手動ステーションの一行判定"),
      body: t(
        "이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다.",
        "This product measures nothing for the steps above — it never shows them as done.",
        "本製品は上記の段階を測定しません — 完了と表示することはありません。",
      ),
    },
    {
      title: t("W74 기본값 우연 일치", "The Vissim-default coincidence is not a measurement", "W74既定値の偶然一致"),
      body: t(
        "주행행태 {N}개가 공표 행과 일치하지만 정지 시 거리가 지침값(2.5 m)이 아닙니다. Vissim 기본값이 마침 {row} pcu 행과 같으므로, 이 일치는 측정이 제출됐다는 증거가 아닙니다.",
        "{N} behaviour(s) match a published row but do not carry the guideline standstill distance (2.5 m). Vissim's default happens to equal the {row} pcu row, so this match is not evidence that a measurement was submitted.",
        "走行挙動{N}件が公表行と一致しますが、停止時距離が指針値(2.5 m)ではありません。Vissimの既定値がちょうど{row} pcu行と同じため、この一致は測定が提出された証拠にはなりません。",
      ),
    },
    {
      title: t("제안 배치 — 미매핑 도구", "Proposal placement — unmapped tools", "提案の配置 — 未対応ツール"),
      body: t(
        "이 제안의 편집 도구는 §2.12.1의 어느 단계에도 매핑되어 있지 않아 여기(채점·진단)에 둡니다 — 단계를 지어내지 않습니다.",
        "This proposal's edit tools map to no §2.12.1 step, so it is shown here (scoring & diagnosis) — the step is not invented.",
        "この提案の編集ツールは§2.12.1のいずれの段階にも対応付けられていないため、ここ（採点・診断）に表示します — 段階を作り出しません。",
      ),
    },
    {
      title: t("제안 배치 — 스테이션 미해결 폴백", "Proposal placement — station fallback", "提案の配置 — ステーション未解決"),
      body: t(
        "이 제안은 단계 {step}에 해당하지만 그 단계의 스테이션을 찾지 못해 여기(채점·진단)에 둡니다.",
        "This proposal belongs to step {step}, whose station could not be resolved, so it is shown here (scoring & diagnosis).",
        "この提案は段階{step}に属しますが、その段階のステーションを解決できなかったため、ここ（採点・診断）に表示します。",
      ),
    },
    {
      title: t("제안 배치 — 다단계 제안", "Proposal placement — multi-step proposals", "提案の配置 — 複数段階の提案"),
      body: t(
        "이 제안은 다른 단계의 조치도 함께 포함합니다.",
        "This proposal also carries actions belonging to other steps.",
        "この提案には他の段階の操作も含まれます。",
      ),
    },
    {
      title: t("빈 상태 — 다른 단계에 대기 중일 때", "An empty proposal list — waiting elsewhere", "空の状態 — 他の段階で待機中"),
      body: t(
        "이 단계에는 승인을 기다리는 제안이 없습니다. 다른 단계에 {N}건이 대기 중입니다 — 왼쪽 레일의 파란 배지를 보세요.",
        "No proposal awaits approval at this step. {N} are waiting at other steps — see the blue badges on the left rail.",
        "この段階では承認待ちの提案はありません。他の段階で{N}件が待機中です — 左レールの青いバッジをご覧ください。",
      ),
    },
    {
      title: t("빈 상태 — 아무 데도 없을 때", "An empty proposal list — nothing anywhere", "空の状態 — どこにもないとき"),
      body: t(
        "승인을 기다리는 제안이 없습니다. 제안은 어시스턴트가 만들며, 만들어지면 해당 단계에 나타납니다.",
        "No proposal is waiting for approval. Proposals are produced by the assistant, and appear at their own step when they are.",
        "承認待ちの提案はありません。提案はアシスタントが作成し、作成されると該当する段階に表示されます。",
      ),
    },
    {
      title: t("기준 모델 검증 탭", "The base-model validation tab", "基準モデル検証タブ"),
      body: t(
        "AM/PM 기본 시나리오의 교통량, GEH, 통행시간 검증 결과를 확인합니다.",
        "Validate AM/PM base scenarios with traffic volume, GEH, and travel time checks.",
        "AM/PM基本シナリオの交通量・GEH・旅行時間の検証結果を確認します。",
      ),
    },
    {
      title: t("검증 항목 커버리지 카드", "The criteria-coverage card", "検証項目カバレッジカード"),
      body: t(
        "이 페이지가 실제로 평가한 항목과 평가할 수 없는 항목을 구분해 표시합니다.",
        "Which criteria this page actually evaluated, and which it cannot.",
        "このページが実際に評価した項目と評価できない項目を区別して表示します。",
      ),
    },
    {
      title: t(
        "Critical 교차로 기준이 별도인 이유 (§5.6.3.1, p.213)",
        "Why the critical-intersection criterion is separate (§5.6.3.1, p.213)",
        "Critical 交差点の基準が別である理由 (§5.6.3.1, p.213)",
      ),
      body: t(
        "네트워크 전체 기준({profile})을 만족하더라도, 지정한 critical 교차로는 개별적으로 GEH < {X} 를 만족해야 합니다. 통과율 {Y}% 로는 이 조건이 충족되지 않습니다.",
        "Even when the network-wide {profile} criteria are met, each designated critical intersection must individually achieve GEH < {X}. The {Y}% pass rate does not satisfy this.",
        "ネットワーク全体の基準（{profile}）を満たしていても、指定したcritical交差点は個別にGEH < {X} を満たす必要があります。通過率 {Y}% ではこの条件は満たされません。",
      ),
    },
    // D16-a/b/c. The ⑫ guardrail machinery. Its SCREEN was removed by the user's
    // 2026-08-20 decision ("developer side information... get rid of it") and its
    // BACKEND was not touched by one line: the refusals and the grants are
    // enforced in backend/services/agent/parameter_search.py,
    // searched_behaviour_broker.py and gates.py, and the server still sends every
    // field these three entries describe. Only the rendering went.
    //
    // All three are verbatim ko/en/ja triples in
    // docs/product/calibration-doctrine.md, exactly like every other item here,
    // and the contract suite cross-pins all NINE sentences in both files. (They
    // were briefly the exception: D16 landed in the knowledge file as Korean
    // prose, so these entries carried the only en/ja that existed. The knowledge
    // file caught up on 2026-08-20 and the two copies are now byte-identical —
    // which is the only state the cross-pins accept.)
    {
      title: t("탐색 산출물의 가드레일 — 두 경로 예보", "The guardrail on a searched value — the two-path forecast", "探索産出物のガードレール — 二経路の予報"),
      body: t(
        "탐색이 산출한 값이 모델에 닿는 길은 두 가지이고, 예보는 언제나 두 줄이었습니다. 직접 발행(direct_edit) — 플래너가 공유 행태 파라미터를 직접 쓰려 하면 공유 행태 가드레일이 이름을 명명하며 거부합니다 (constraint SHARED_BEHAVIOR_REFUSAL). 승인 경로(approved_set) — 완료된 탐색의 승자를 승인 카드로 승인하면 원장에서 grant가 재발급되어 1회 소모로 적용됩니다. 이 경로의 조건 코드는 RADIUS_NAMED_IN_FULL — 동의 반경을 전부 명명할 수 없으면 예외가 성립하지 않습니다 (ko 표기: '통과 (반경 전부 명명 시)'). 예보 상태는 3값입니다: 거부 예상 / 통과 예상 / 미지 — 조건 코드 GUARDRAIL_WILL_REFUSE · GUARDRAIL_CLEAR · GUARDRAIL_UNKNOWN.",
        "A searched value reaches the model by two roads, so the forecast was always two lines. Issued directly (direct_edit) — a planner writing a shared behaviour parameter is refused BY NAME by the shared-behaviour guardrail (constraint SHARED_BEHAVIOR_REFUSAL). Through the approval machinery (approved_set) — approving a finished search's winner on its approval card re-mints the grant from the ledger and spends it once. That road's condition code is RADIUS_NAMED_IN_FULL: the exception does not hold unless the consent radius can be named in full. The forecast has three states — expected to refuse, expected to pass, and not forecast — carried by GUARDRAIL_WILL_REFUSE, GUARDRAIL_CLEAR and GUARDRAIL_UNKNOWN.",
        "探索が算出した値がモデルに届く経路は二つあり、予報は常に二行でした。単独発行(direct_edit) — プランナーが共有挙動パラメータを直接書こうとすると、共有挙動ガードレールが名指しで拒否します (constraint SHARED_BEHAVIOR_REFUSAL)。承認経路(approved_set) — 完了した探索の勝者を承認カードで承認すると、台帳から grant が再発行され1回消費で適用されます。この経路の条件コードは RADIUS_NAMED_IN_FULL で、同意半径を完全に命名できなければ例外は成立しません。予報の状態は3値です: 拒否見込み / 通過見込み / 未予報 — 条件コードは GUARDRAIL_WILL_REFUSE · GUARDRAIL_CLEAR · GUARDRAIL_UNKNOWN。",
      ),
    },
    {
      title: t("탐색 산출물의 가드레일 — 결정 칩", "The guardrail on a searched value — the resolution chips", "探索産出物のガードレール — 決定チップ"),
      body: t(
        "가드레일 충돌의 해소는 사용자 결정이며, 각 해소안의 상태(REJECTED_BY_USER 등)는 서버가 구동합니다. 문장과 상태 단어는 전부 서버의 것이었고, 지금도 서버 페이로드에 그대로 있습니다 — 화면이 렌더하지 않을 뿐입니다.",
        "Resolving a guardrail collision is the USER's decision, and each resolution's status (REJECTED_BY_USER and its kin) is driven by the server. Every sentence and every status word was the server's, and every one of them is still in the server payload — the screen simply no longer renders them.",
        "ガードレール衝突の解消はユーザーの決定であり、各解消案の状態(REJECTED_BY_USER など)はサーバーが駆動します。文章も状態語もすべてサーバーのものであり、今もサーバーのペイロードにそのまま存在します — 画面が描画しないだけです。",
      ),
    },
    {
      title: t("탐색 산출물의 가드레일 — 증거 등급", "The guardrail on a searched value — evidence rank", "探索産出物のガードレール — 証拠等級"),
      body: t(
        "탐색 산출물은 Table 5-6 산출물보다 한 단계 낮은 증거입니다 — 근거문(원장 digest·홀드아웃 seed 탑재)이 스스로 명시합니다. 이 문장은 서버가 합성하며, 승인 카드 표면에 계속 표시됩니다 — 승인 결정에 필요한 정직성이므로 #50-d에서도 퇴거되지 않았습니다.",
        "A searched value is evidence one rank below a Table 5-6 value, and the backend's own sentence — carrying the ledger digest and the holdout seed — says so. The server composes it, and it still renders on the surface of the approval card: it is honesty the approval decision needs, so #50-d did not evict it.",
        "探索産出物は Table 5-6 の産出物より一段低い証拠であり、根拠文（台帳の digest・ホールドアウトの seed を含む）が自ら明示します。この文章はサーバーが合成し、承認カードの表面に表示され続けます — 承認判断に必要な誠実さであるため、#50-d でも撤去されていません。",
      ),
    },
    {
      // D18 (unit #53, 2026-08-24). The sixth status word's RULE, and the one
      // sentence the calibration screen must never carry: the screen states
      // what the act records (name, time, network hash) beside the button that
      // performs it, and WHY a signature is not 완료 lives here. Byte-identical
      // to docs/product/calibration-doctrine.md's D18 — the contract suite
      // compares the two files' triples as sets, in all three languages.
      title: t("사람 증언은 완료가 아니다", "A human attestation is not Done", "人の証言は完了ではない"),
      body: t(
        "확인됨(사람 증언)은 완료가 아닙니다 — 사람이 서명한 단계이지 이 제품이 측정한 단계가 아니며, 서명한 네트워크가 바뀌면 다시 확인해야 합니다.",
        "Confirmed (human attestation) is not Done — it means a named person signed for the step, not that this product measured it, and it must be made again once the network they signed against changes.",
        "確認済み（人の証言）は完了ではありません — 人が署名した段階であり、本製品が測定した段階ではありません。署名した時点のネットワークが変われば、改めて確認が必要です。",
      ),
    },
  ];
}

/**
 * /genesis/jobs/[id] -- one generation's detail and its verdict. These three
 * entries have moved twice: out of inline ⓘ tooltips on the hub (U-1-quick),
 * onto the hub's own drawer, then onto the detail route the R1.5 grid actually
 * lives on (unit #23). Verbatim through every move.
 */
export function jobHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("판정 게이트", "The validation gate", "判定ゲート"),
      body: t(
        "R1.5 검증 게이트 V1–V9. 실행되지 못한 검사는 절대 통과가 아닙니다. 거부 등급 검사가 결함을 입증하면 그 네트워크는 프로젝트에 등록되지 않습니다 — 파일과 판정은 아티팩트에서 그대로 받아볼 수 있습니다.",
        "The R1.5 validation gate, V1–V9. A check that could not run is never a pass. When a refuse-tier check proves a fault the network is NOT registered in the project — the file and the verdict stay downloadable from the artifacts.",
        "R1.5検証ゲート V1–V9。実行できなかった検査は決して合格ではありません。拒否ティアの検査が欠陥を証明した場合、そのネットワークはプロジェクトに登録されません — ファイルと判定はアーティファクトからそのまま取得できます。",
      ),
    },
    {
      title: t("잠정 등록", "Provisional registration", "暫定登録"),
      body: t(
        "프로젝트에 등록되었습니다. 다만 실행되지 못한 거부 등급 검사가 있어 잠정 상태입니다 — 생성 잡은 Vissim COM을 열 수 없으므로, 실제 Vissim이 이 파일을 여는 것을 증명하는 V1은 라이브 검사 증거가 있어야 확정됩니다. 증거는 두 가지 경로로 들어옵니다: 운영자가 직접 돌린 검사 결과를 첨부하거나(운영자 진술), 판정 화면의 V1 칸에서 '실 Vissim으로 확인'을 눌러 이 앱이 작업 큐에서 직접 열어 보게 하는 것입니다.",
        "Registered in the project, but provisional: a refuse-tier check could not run. A generation job may not open Vissim COM, so V1 — the only check that proves a real Vissim opens this file — stays open until live-check evidence exists. It arrives two ways: an operator attaches the result of a check they ran themselves (their attestation), or the V1 cell's own \"실 Vissim으로 확인\" button has this app open the file on its job queue.",
        "プロジェクトに登録されましたが、実行できなかった拒否ティア検査があるため暫定です。生成ジョブは Vissim COM を開けないため、実際の Vissim がこのファイルを開くことを証明する V1 はライブ検査の証拠がなければ確定しません。証拠は二通りで入ります：運用者が自分で実行した結果を添付する（運用者の申告）か、判定画面の V1 セルの「実Vissimで確認」ボタンでこのアプリにジョブキュー上で開かせるかです。",
      ),
    },
    {
      title: t("지도", "The map", "地図"),
      body: t(
        "지도는 이 생성이 기록한 좌표를 그립니다. 초안이 OpenStreetMap 좌표에서 만들어졌다면 서버가 역변환한 실제 WGS84 좌표를 배경지도 위에 그리고, 그렇지 않다면 배경지도 없이 이 생성만의 로컬 좌표로 그립니다 — 어느 쪽인지는 지도 제목 옆 ⓘ에 그때그때 표시됩니다.",
        "The map draws the coordinates this generation recorded. If the draft was built from OpenStreetMap degrees, the server's own inverse projection places it on a real WGS84 basemap; otherwise it is drawn with no basemap, in this generation's own local coordinates — which one applies is stated by the ⓘ beside the map's own title.",
        "地図はこの生成が記録した座標を描きます。下書きが OpenStreetMap の座標から作られていれば、サーバーが逆変換した実際の WGS84 座標を背景地図の上に描き、そうでなければ背景地図なしでこの生成専用のローカル座標で描きます — どちらであるかは地図タイトル横の ⓘ にその都度表示されます。",
      ),
    },
  ];
}
