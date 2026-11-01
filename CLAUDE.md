# 論文読み house rules（このディレクトリ専用）

このディレクトリで論文を HTML explainer として説明するときの **追加ルール**（Claude Code 用。
Codex 用の `AGENTS.md` に同内容がある。両方を同時に直すこと）。

執筆規約の土台は **この repo に pin 同梱した `skills/explain-via-html/SKILL.md`** とする。
先にそれを読み、その規約を守った上で、以下を上乗せする。**衝突した場合はこのファイルを優先する。**

- **なぜ marketplace ではなく repo 同梱なのか。** 上流 `mathbullet/skills` は 2026-08-01 の
  コミット `5dc38549` で `explain-via-html` を削除し、別物の `html` スキル（MathJax 3 固定・
  外部 CSS 同梱・別配色）に置き換えた。marketplace から入れ直しても入手できず、`html` は
  §1 の KaTeX 方針とも衝突する。経緯と pin 情報は `skills/README.md`。
  SK の Claude Code にはまだ旧 plugin 版が入っているが、**同内容（sha256 一致）** なので
  二重に適用されても矛盾しない。
- **同梱スキル本体は書き換えない。** プロジェクト固有の規約はこのファイルに書く。

## 1. モデル・手法・設定は数式で丁寧に説明する

論文の中核（モデル定義・損失関数・学習設定・推論手続き・主要ハイパラ）は、文章だけで流さず **数式を中心** に説明する。

- **未定義語・未定義記号をゼロにする。** 数式に現れる記号はすべて初出時に定義する。広く知られた記号でも省略しない。
- **テンソル・ベクトルは shape を明記する。** 例: 入力 $X \in \mathbb{R}^{B \times T \times d}$（$B$ = バッチサイズ、$T$ = 系列長、$d$ = 埋め込み次元）。
- **「丁寧に・詳しく」を最優先。** 1〜2 行で済ませない。なぜその式になるのか、各項が何をしているのか、どの操作がどの次元に効くのかまで噛み砕く。読者はその論文を初見と仮定する。
- **設定・ハイパラは具体値とともに。** 学習率・バッチサイズ・最適化器・スケジュール等は論文記載の具体値を表で示し、式中の記号と対応づける。
- **数式のレンダリング。** HTML 内の数式は KaTeX または MathJax を CDN 1 本で読み込んでレンダリングする（`explain-via-html` の自己完結方針は維持しつつ、数式用 CDN の使用のみ許可する）。**既定は KaTeX**（MathJax はデフォルトでインライン `$ … $` を読まないぶん事故りやすい）。インライン数式・ブロック数式を適切に使い分ける。
- **数式の中に生の `<` を書かない。最重要。** `<` の直後が英字・`/`・`!`・`?` のとき、ブラウザの HTML トークナイザはそれを**開始タグ**として読む。KaTeX が動く前の段階で起きるので、数式どころか**周囲の本文ごとタグの属性に吸い込まれて画面から消える**。
  - 例: `<li>$c_{<t} := (c_1,\dots,c_{t-1})$: 先頭 $t-1$ 個の …</li>` と書くと `<t}` が開始タグ名 `t}` になり、`:= (c_1,\dots,c_{t-1})$: 先頭 $t-1$ 個の` が消える（headless Chrome の DOM に `</t}$>` `</t};x)>` という要素が実在するのを 2026-08-16 に確認）。
  - **正しい書き方**: `$c_{&lt;t}$` あるいは `$c_{\lt t}$`。`&lt;` はブラウザがテキストノードに戻してから KaTeX に渡るので TeX の意味は変わらない。
  - `x_{<t}` / `c_{<t}` / `a_{<i}` は言語モデル系論文の標準記法なので**必ず踏む**。`$a < b$`（`<` の次が空白）や `$\rho(A)<1$`（次が数字）は無害なのでそのままでよい。
- **KaTeX が実装していない LaTeX を使わない。** 使うと生の TeX ソースがそのまま出るか赤字になる。代表例と代替:
  `\textsc` → `\mathrm` / `\text`、`\begin{psmallmatrix}` → `\left(\begin{smallmatrix}…\end{smallmatrix}\right)`、
  `\begin{align}` → `\begin{aligned}`、`\begin{eqnarray}` → `\begin{aligned}`、`\bm` → `\boldsymbol`、
  `\mathds` → `\mathbb`、`\nicefrac` → `\tfrac`、`\mbox` → `\text`、`\eqref` / `\label` → 削除。
- **本文にリテラルの `$`（金額・シェルプロンプト等）を書かない。** `\$` は**エスケープにならない**。KaTeX の auto-render は TeX を解釈する前に生の `$` で本文を切るので、`$\$2$` は 2 個目の `$` で切れて本体 `\` を KaTeX に渡し、パースエラーになる。`2 USD` のように**記号を使わない言い回しに書き換える**。
- **本文の数式デリミタは `$$`／`$` で書く。** ブロック数式 = `$$ … $$`、インライン = `$ … $`。本文で `\[ … \]`／`\( … \)` を**使わない**。理由: KaTeX `renderMathInElement` のデフォルト設定は `$$`/`$` しか登録せず、`\[ … \]` は**リテラル文字列のまま表示**されてしまう（実際に過去ファイルで多発した）。
- **KaTeX 設定には保険として4種すべてのデリミタを登録する。** 生成する `renderMathInElement(document.body, {…})` の `delimiters` には、`$$`(display) / `$`(inline) に加えて `\[`(display) / `\(`(inline) も必ず入れる。これでうっかり `\[…\]` を書いてもレンダリングされる。正準ブロック:
  ```js
  delimiters: [
    {left: "$$", right: "$$", display: true},
    {left: "$",  right: "$",  display: false},
    {left: "\\[", right: "\\]", display: true},
    {left: "\\(", right: "\\)", display: false}
  ]
  ```
- **MathJax を使う場合も `$` インラインを有効化する。** MathJax tex-svg/tex-chtml のデフォルトはインライン `$ … $` を**レンダリングしない**（`\( … \)` のみ）。本文を `$ … $` で書く方針と整合させるため、MathJax 設定に明示的に登録する: `window.MathJax = { tex: { inlineMath: [['$','$'],['\\(','\\)']], displayMath: [['$$','$$'],['\\[','\\]']] } };`。迷ったら KaTeX（上の4種デリミタ構成）を既定にする。
- **図・表キャプション内の数式は必ずインライン。** `<figcaption>` の中では `$$ … $$` や `\[ … \]`（中央寄せのブロック数式）を使わず、必ずインライン数式（MathJax は `\( … \)`、KaTeX は `$ … $`）で書く。
- **MathJax(tex-svg) を使う場合の CSS 落とし穴。** 図ダイアグラム用に `figure svg { display:block; margin:0 auto }` を書くと、`<figcaption>` 内のインライン数式が MathJax の出力する `<svg>` まで巻き込み、数式が1個ずつ中央寄せの独立ブロックになる。これを防ぐため、より詳細度の高い上書き
  ```css
  figure figcaption svg { display: inline; margin: 0; vertical-align: middle; }
  figure figcaption mjx-container { display: inline-block; margin: 0; }
  ```
  を `</style>` 直前に必ず入れる（図ダイアグラムの中央寄せは `figure svg` 側で維持される）。KaTeX はインライン数式を `<span>` で出すため影響を受けないが、この上書きを入れておいても無害。

## 1.4. 書き終えたら必ず `check_html.py` を通す

上のルールは「守れ」と書くだけでは守り切れない（2026-08-16 の全数監査で 103 本中 13 本に数式破損が残っていた）ので、
**機械検査を必ず 1 回通してから完了とする**。

```bash
python3 tools/readpaper/host/check_html.py --fix html-expl/<生成したファイル>.html
```

- `--fix` が自動修復するもの: 数式中の生 `<` → `&lt;`、デリミタ登録の正準化、`throwOnError: false`、`<figcaption>` 内ブロック数式のインライン降格。
- 自動修復**できない**もの（手で直す）: `katex-unsupported`（非対応マクロ）、`stray-dollar` / `escaped-dollar`（リテラルの `$`）、`swallowed-prose`（デリミタの閉じ忘れ）、`no-renderer`。
- **findings が 0 になるまで直す。** 終了コードは findings が残っていれば 1。
- readpaper worker（ワンクリック）は非公開の作業版をfinalize後に検査し、findingsが残ったら公開を中止して失敗通知を出す。対話生成でも Stop hook が同じ処理を走らせる（§1.5）が、**自分で 1 回叩いて確認すること**。

## 1.5. claude-code セッション id をタイトル下に可視で出す

担当した論文の claude-code セッション id を、HTML コメントではなく **ヘッダの `<div class="meta">`（タイトル直下）に可視テキスト**として載せる。後から `claude --resume <id>` でその論文のセッションを再開できるようにするため。

- 例: `<br>claude-code session: <code>__AGENT_SESSION_ID__</code>`
- **常にリテラル文字列 `__AGENT_SESSION_ID__` を書く。headless でも対話でも同じ。**
  実 id への置換は必ず**決定論的なコード**が行う。書き手（agent）は id を推測しない。
  - **headless（Chrome ワンクリック）**: worker（`tools/readpaper/host/readpaper_run.sh`）が `finalize_html.py` を呼び、事前採番した実セッション id へ置換する。プレースホルダを書き忘れた場合は `</h1>` 直後に可視行をフォールバック注入する。
  - **対話セッション**: `Stop` hook（`.claude/settings.json` → `tools/readpaper/hooks/explainer_stop_hook.sh`）が置換する。hook には stdin JSON で本物の `session_id` が渡るので、これが唯一の正しい取得経路。
- **自分のセッション id を書こうとしてはいけない。**（旧ルールはこれを要求していて、それが破綻の原因だった。）
  対話中の Claude Code は自分のトランスクリプト UUID（`~/.claude/projects/<proj>/<uuid>.jsonl` のファイル名）を知らない。system prompt に出る `session_…` は **claude.ai 側の別体系**で、`claude --resume` は受け付けない。
  2026-08-16 の監査では、対話生成された 51 本のうち **15 本がどのログにも存在しない UUID**（=でっち上げ）を持ち、1 本は id 行そのものが欠落していた。
- **プレースホルダのトークンは `__AGENT_SESSION_ID__`。** 旧トークン `__CLAUDE_SESSION_ID__` も `finalize_html.py` が引き続き受け付ける（既存 HTML 約100本との互換のため）が、**新規生成では `__AGENT_SESSION_ID__` を使う**。同じ worker が Codex バックエンドでも走るため、CLI 名を含まない中立トークンにしてある。
- **置換は python（`finalize_html.py`）で行う。** 旧実装は bash の `ls -t html-expl/*.html | grep | head` で出力ファイルを発見していたが、Chrome 配下の macOS-TCC ゆらぎでこの glob が空を返し、置換・stamp が丸ごと skip されて**リテラル `__CLAUDE_SESSION_ID__` が出荷される事故が多発した**。現在は明示された作業HTMLをPythonで直接読み、置換する。
- **出力ファイルは実行開始前に固定する（2026-09-07）。** worker が `logs/readpaper-work/<run-id>/html-expl/<name>.html` を編集先として明示する。そのファイルだけに生成し、別名を作らない。finalizer は `--html <exact-path>` でそのファイルだけを処理し、本文のリンク・図パス・mtimeから探索しない。公開用HTMLへのコピー・移動・一覧更新はworkerが検査後に行う。

## 1.6. 図・表は「貼る前に検証してから貼る」

**headless worker の作業先（2026-09-07）:** worker実行では、プロンプトに示された非公開のFIGDIRにmanifestと図がある。そこを確認し、HTML内の画像URLには指定された `figs/<dest>/<run-id>/<file>` を用いる。公開先の既存画像を書き換えない。以下の `html-expl/figs/<arxiv-id>/` は旧版と通常の対話生成の配置を指す。


論文図・表は readpaper worker（`tools/readpaper/extract_figures.py`）が機械的に切り出して
`html-expl/figs/<arxiv-id>/manifest.json` ＋ PNG に置く。**機械切り出しは外すことがある**ので、
そのまま貼らず検証する。manifest 各エントリ: `label/num/page/file/clip/caption/low_conf/needs_review/reason`。

- **`needs_review=false`** の crop → そのまま `<img src="figs/<id>/<file>">` で埋め込んでよい。
- **`needs_review=true`** の crop（`reason` に理由）→ **PNG を Read（vision）して caption と内容が一致するか必ず確認**。
  - 一致 → 埋め込む。
  - ズレ（本文テキストを掴んでいる／図の一部しか写っていない／右・下が切れている 等）→ **貼る前に「本当にこの clip でよいか」考え直す**。
    `pdftoppm -png -f <page> -l <page> -r 150 <pdf> <prefix>` でページを起こし、PIL で正しい領域に切り直し → 再 Read で確認、を満足するまで反復。
  - 表が論文中で**実体プローズ**（本物の表組み画像が無い）の場合は、誤クロップを捨てて数値を HTML `<table>` に再組版する。
    まず PDF を見て本物の表画像が存在するか確かめ、あれば正しい領域を再クロップ、無ければ HTML 表へフォールバック。
- **図が実在するのに inline SVG / canvas で論文図を描き直すのは禁止**（manifest が空＝抽出0件の論文に限り、最終手段として SVG 模式図を許可）。
- 失敗モードの既知例: low_conf 固定窓 fallback（表は本文テキスト、図は誤バンドを掴む）／罫線がデータ幅より狭く右列が切れる／
  バンドが極端に薄く図の一部しか写らない。`reason` 文がどれかを示す。

## 1.7. 核心コンセプト・核心アルゴリズムには初出時に「具体例つきの丁寧な補足」を入れる

論文には「これがわからないと以降が全部意味不明になる」前提概念・前提アルゴリズムが必ずある
（例: その分野特有の問題設定、独自に定義した記号体系、提案手法の土台になる既存手法、
中核となる損失・更新則・探索手続き）。これらは **最初の生成時点で**、本文中の初出箇所に
**具体例を交えた丁寧な補足説明（一種の「ミニ解説ボックス」）を必ず差し込む**。後追いの Q&A
（§2）任せにしない。

- **何を「核心」とみなすか。** 読者がその論文を初見と仮定して、「ここを飛ばすと次段落以降の
  式・主張が追えなくなる」ものを核心とする。典型例:
  - 問題設定そのもの（何を入力とし何を出力とするタスクか、何が難しいのか）。
  - 論文が独自に導入した記法・データ構造（例: トークン化規約 `(r,c,v)`、盤面の部分構造単位、
    集合値写像 $F:K\rightrightarrows Y$ 等）。
  - 提案手法が土台にする既存手法（例: MDLM, flow matching, looped Transformer, ETF 等）。
    これを知らない読者のために**一段落で要点を自己完結的に**書く。
  - 中核アルゴリズム（損失・更新則・探索/推論手続き）の **1 ステップを最小例で手計算**して見せる。
- **具体例の作り方（最重要）。** 抽象的な式を出したら、**小さな具体ケースに数値を代入して
  「実際にこう動く」を1回トレースする**。例:
  - 系列タスクなら $T=3$ 程度の最小系列で、各ステップのトークン・状態・損失対象を実際に並べる。
  - 行列・テンソル演算なら $2\times2$ や $3\times3$ の具体行列を置いて積を手で展開する。
  - 探索/反復アルゴリズムなら、3〜4 要素の toy 入力で最初の 1〜2 反復を表で追う。
  - 確率・損失なら、具体的な確率値（例 $p=0.7$）を入れて数値を出す。
- **置き方。** 本文の流れを切らないよう、核心概念の初出直後に視覚的に区別できるブロック
  （例: `<aside class="concept">` や色付き callout ボックス）で囲む。見出しは
  「直感」「最小例」「なぜこうなるのか」等、何の補足かが一目でわかる短いラベルにする。
  §1 の数式方針（未定義記号ゼロ・shape 明記・具体値）はこの補足ボックス内でも全面的に守る。
- **「丁寧さ」の水準。** その概念だけで完結して理解できること。前提知識を仮定して 1〜2 行で
  済ませない。「定義 → なぜ必要か → 最小具体例での挙動 → 論文本体での使われ方」まで繋げる。
- **やりすぎ注意。** 全部の式に補足を付けるのではなく、**核心に絞る**。枝葉の式・標準的な式
  （普通の softmax, 普通の cross-entropy 等）には付けない。判断基準は「初見読者がここで詰まるか」。

## 2. 読後 Q&A を HTML 末尾に追記する

論文を読み終えた後に SK が投げた質問とその回答を、HTML の **いちばん最後** に「Q&A」セクションとしてまとめる。

- 各エントリは **「質問（SK の原文をそのまま）→ 回答」** の形式。
- 回答も §1 と同じ方針で書く（数式中心・未定義語ゼロ・丁寧）。
- 既存の Q&A がある HTML を更新する場合、過去分を消さず **日付つきの小見出しで追記** する（例: `### 2026-06-03`）。古い Q&A を上書きしない。
- 質問が本文の記述で既に説明済みの内容に及ぶ場合は、本文の該当箇所へリンク（アンカー）を張り、Q&A 側では差分・補足のみを書く。

## 3. `html-expl/meta/` は SK の欄。agent は読むだけで、編集しない

`html-expl/meta/<stem>.json` には SK 自身が記事内カードから付けた **読書状態（読み中／読了）・
理解度（ふんわり／だいたい／しっかり）・ひとことまとめ** が入っている（`tools/readpaper/host/meta_store.py`）。
in-page chat の prompt にはこれが読み取り専用で添えられる（`ask_run.py`）。

- **この欄は SK の言葉であることに価値がある。** agent が要約を書き足したり、理解度を「更新」したりしてはならない。
  誤解に気づいたら Q&A や本文で指摘する。
- **書き込み経路は `POST /api/meta` だけ。** JSON を直接編集しない。`_all.json` は保存のたびにサーバが再生成する。
- 全件の書き出しは `python3 tools/readpaper/export_reading.py`（md 表）。設計は `notes/20260904-reading-marks-plan.md`。

- **`html-expl/ink/<stem>.json` も同じ扱い（2026-09-07）。** SK が Apple Pencil で記事に書いた手書きストローク（`tools/readpaper/host/ink_store.py`、書き込みは `POST /api/ink` のみ）。agent は読まず、編集もしない。chat の prompt にも添えない。
- **`html-expl/pos/<stem>.json` も同じ扱い（2026-09-09）。** SK が最後に読んでいた位置（しおり、`tools/readpaper/host/pos_store.py`、書き込みは `POST /api/pos` のみ、`pos_widget.js` が自動保存・自動復帰）。agent は読まず、編集もしない。chat の prompt にも添えない。
