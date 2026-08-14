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
 * /genesis/phases -- the 현시도 review. `확인 절차` restates the same doctrine
 * the page's own always-visible intro paragraph carries; that paragraph stays on
 * the page (it is that screen's foundational claim, not a tooltip), and this is
 * the copy a reader finds when they come to the documentation instead.
 */
export function phasesHelpEntries(t: Translate): HelpEntry[] {
  return [
    {
      title: t("확인 절차", "The confirmation step", "確認手順"),
      body: t(
        "현시도는 그림이므로 어떤 파싱도 접근로–현시 배정을 산출할 수 없습니다. 모델의 판독은 제안일 뿐이며, 확인 버튼을 누른 사람의 진술만이 실제 신호 입력이 됩니다 — 현시도는 사람 확인만이 사실을 만듭니다.",
        "The 현시도 is a drawing, so no parse can produce the approach→phase mapping. The model's reading is only a proposal; only the statement of the person who confirms becomes actual signal input — for a 현시도, only a human confirmation makes a fact.",
        "現示図は図面であり、解析では流入路と現示の対応を得られません。モデルの判読は提案に過ぎず、確認した人の申告のみが実際の信号入力になります — 現示図は人の確認だけが事実を作ります。",
      ),
    },
    {
      title: t("signals 입력", "Signals input", "信号入力"),
      body: t(
        "확인된(CONFIRMED) 교차로만 signals 입력에 포함됩니다 — 미확인이거나 재확인이 필요한 교차로는 빠집니다.",
        "Only CONFIRMED intersections are included in the signals input — an unconfirmed or re-confirmation-needed intersection is left out.",
        "確認済み（CONFIRMED）の交差点のみが信号入力に含まれます — 未確認または再確認が必要な交差点は除外されます。",
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
        "프로젝트에 등록되었습니다. 다만 실행되지 못한 거부 등급 검사가 있어 잠정 상태입니다 — 생성 잡은 Vissim COM을 열 수 없으므로, 실제 Vissim이 이 파일을 여는 것을 증명하는 V1은 라이브 검사 증거를 붙여야 확정됩니다.",
        "Registered in the project, but provisional: a refuse-tier check could not run. A generation job may not open Vissim COM, so V1 — the only check that proves a real Vissim opens this file — stays open until live-check evidence is attached.",
        "プロジェクトに登録されましたが、実行できなかった拒否ティア検査があるため暫定です。生成ジョブは Vissim COM を開けないため、実際の Vissim がこのファイルを開くことを証明する V1 はライブ検査の証拠を添付するまで確定しません。",
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
