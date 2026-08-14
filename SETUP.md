# セットアップ手順（新しい Mac に一から入れる）

Chrome で論文のページを開いてツールバーの **DP** ボタンを1回押すと、PDF を落として
AI エージェントに読ませ、数式つきの HTML 解説を自動生成して通知する、という道具です。
この文書はそれを新しい Mac にゼロから入れる手順です。所要 15〜30 分（ダウンロード時間を除く）。

生成された解説は `html-expl/` に貯まり、`html-expl/index.html` が一覧になります。

---

## 0. 前提

| | |
|---|---|
| OS | **macOS**（通知に `osascript`、拡張の登録先に `~/Library/...` を使うため） |
| ブラウザ | **Google Chrome** |
| エージェント | **Codex CLI** または **Claude Code** のどちらか一方 |

Windows / Linux、Edge / Firefox は現状そのままでは動きません（`tools/readpaper/install.sh`
の書き直しが必要）。

**エージェントは有料アカウントが要ります。** 論文1本の生成で 20万〜43万トークン規模を
消費します（SK 環境での実測）。読む量に応じて課金される点に注意してください。

---

## 1. Homebrew を入れる

すでに入っていれば飛ばしてください（`brew --version` で確認）。

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

インストール後、画面の最後に出る `eval "$(/opt/homebrew/bin/brew shellenv)"` 系の
2〜3 行を指示どおり実行してください（PATH を通す作業です）。

## 2. PDF 処理系と Python を入れる

```bash
brew install poppler python
```

- **poppler** — `pdftotext` と `pdftoppm` が入ります。論文 PDF から図・表を
  画像として切り出すのに使います。無いと図が入りません。
- **python** — 図の切り出し（PIL で crop）と HTML 索引の生成に使います。

続けて Python ライブラリ:

```bash
python3 -m pip install --user pillow numpy
```

確認:

```bash
pdftotext -v && pdftoppm -v && python3 -c "import PIL, numpy; print('ok')"
```

## 3. エージェント CLI を入れてログインする

### Codex CLI を使う場合

```bash
brew install codex     # または npm i -g @openai/codex
codex login
```

`codex login` はブラウザが開くので ChatGPT アカウントでログインします。確認:

```bash
codex exec --skip-git-repo-check "say hi" </dev/null
```

### Claude Code を使う場合

https://claude.com/claude-code の手順でインストールし、`claude` を一度起動して
ログインします。確認: `claude -p "say hi"`

> 両方入っている場合は `claude` が優先されます。切り替え方は §8。

## 4. リポジトリを clone する

置き場所はどこでも構いません（`~/Desktop` の下でなくても動きます）。
**clone したディレクトリがそのまま作業ディレクトリになります。**

```bash
git clone git@github.com:shkamiya/readpaper-workspace.git
cd readpaper-workspace
```

SSH 鍵を設定していなければ HTTPS でも構いません:
`git clone https://github.com/shkamiya/readpaper-workspace.git`

中身:

| パス | 役割 |
|---|---|
| `tools/readpaper/` | 拡張本体・native host・worker・図抽出 |
| `skills/explain-via-html/` | HTML 解説の書き方の規約（外部スキルを pin 同梱したもの） |
| `CLAUDE.md` / `AGENTS.md` | このプロジェクト固有の追加ルール（数式の書き方など） |
| `build_index.py` | `html-expl/index.html`（解説の一覧ページ）を作り直す |
| `papers/` `html-expl/` `logs/` | 生成物置き場。git では追跡しません |

## 5. Chrome 拡張を読み込む

1. Chrome で `chrome://extensions` を開く
2. 右上の **デベロッパーモード** を ON
3. **パッケージ化されていない拡張機能を読み込む** をクリック
4. clone したディレクトリの `tools/readpaper/extension/` を選ぶ
5. 表示された拡張の **ID**（`gaobkbeh...` のような 32 文字）をコピーする

## 6. native messaging host を登録する

Chrome 拡張は、そのままでは PC 上のプログラムを起動できません。
「この拡張はこのプログラムを呼んでよい」という登録が要ります。それをやるのが次のコマンドです。

```bash
tools/readpaper/install.sh <さっきコピーした拡張ID>
```

出力に以下が並べば成功です。エラーで止まる場合は §9 を見てください。

```
deployed host : /Users/<you>/Library/Application Support/readpaper/readpaper_host.py
deployed work : .../readpaper_run.sh  (PROJECT_DIR=/Users/<you>/readpaper-workspace)
agent backend : codex  (/opt/homebrew/bin/codex)
baked PATH    : ...
manifest      : .../NativeMessagingHosts/com.sk.readpaper.json
```

> **仕組みのメモ。** macOS は `~/Desktop` `~/Documents` `~/Downloads` の中身への
> アクセスを制限しており（TCC）、Chrome から起動されるプログラムをそこに置くと
> 起動する前に落ちます。そのため `install.sh` は実体を
> `~/Library/Application Support/readpaper/` に**コピー配置**し、そこに clone 先の
> 絶対パスと PATH を焼き込みます。
> **`tools/readpaper/host/` の中身を書き換えたら、必ず `install.sh` を再実行**
> してください（コピー方式なので自動では反映されません）。
> `git pull` で更新を受け取ったときも同様です。

## 7. 動作確認

1. Chrome で arXiv の論文ページを開く（例: `https://arxiv.org/abs/2405.17767`）
2. ツールバーの **DP** ボタンを1回押す
3. 数分〜十数分待つ。完了すると macOS の通知が出て、解説 HTML が自動で開きます

対応している venue: arXiv / OpenReview / PMLR / JMLR / ACL Anthology / NeurIPS / CVF、
および任意の `*.pdf` の URL。

進行状況は `logs/readpaper-<論文id>-<日時>.log` を見れば分かります:

```bash
tail -f logs/readpaper-*.log
```

## 8. エージェントを切り替える

`install.sh` が検出した方が既定になります。一時的に切り替えるには環境変数:

```bash
READPAPER_AGENT=codex tools/readpaper/host/readpaper_run.sh <pdf_url> <dest名>
```

恒久的に変えるには、`~/Library/Application Support/readpaper/config.env` の
`READPAPER_AGENT=` の行を書き換えます。

Codex のモデルや推論の深さを変えたい場合:

```bash
READPAPER_CODEX_ARGS='-m gpt-5.6-sol -c model_reasoning_effort=high'
```

## 9. うまくいかないとき

| 症状 | 見るところ |
|---|---|
| ボタンを押しても何も起きない | `/tmp/readpaper_host_debug.log`（host の起動トレース）と `/tmp/readpaper-spawn.log` |
| 通知は出るが解説ができていない | `logs/readpaper-<id>-*.log` の末尾。`no html generated` なら agent 側の失敗 |
| `already exists: ...` と出る | その論文の解説がすでにある。作り直すなら `READPAPER_FORCE=1` を付けて worker を直接実行 |
| 図が入らず手描き風の図になる | poppler / pillow / numpy が入っているか（§2）。`logs/...` の `extracted N figure crop(s)` が 0 でないか |
| `Operation not permitted` が出る | システム設定 → プライバシーとセキュリティ → **フルディスクアクセス** に **Google Chrome** を追加 |
| `install.sh` が「neither claude nor codex」で止まる | §3 をやり直す。PATH が通っているか `command -v codex` で確認 |
| `git pull` したのに挙動が変わらない | `tools/readpaper/install.sh <拡張ID>` を再実行（§6 のメモ） |

Chrome なしで host 単体を試すこともできます:

```bash
tools/readpaper/test_host.sh --resolve-only          # URL → (PDF url, 論文id) の解決だけ
tools/readpaper/test_host.sh https://arxiv.org/abs/2405.17767
```

## 10. 安全上の注意（重要）

worker はエージェントを **承認プロンプトなし・サンドボックスなし** で起動します
（Codex は `--dangerously-bypass-approvals-and-sandbox`、Claude Code は
`--dangerously-skip-permissions`）。これは図の切り出しや HTML の書き出しを
いちいち確認せずに進めるためです。

つまり **ダウンロードした PDF の中身を、確認なしでファイル操作やコマンド実行ができる
エージェントに読ませています。** 悪意を持って細工された PDF が、エージェントへの指示文を
埋め込んで意図しない動作をさせる（prompt injection）ことが理論上あり得ます。

**arXiv / OpenReview / PMLR / JMLR / ACL / NeurIPS / CVF といった素性の分かる学術サイトの
論文にだけ使ってください。** 出所の不明な PDF には使わないでください。

## 11. （任意）iPhone から読む

同じ Wi-Fi / Tailscale 上の iPhone から解説を読みたい場合、Mac 側で簡易サーバを立てます:

```bash
cd html-expl && python3 -m http.server 8770 --bind 0.0.0.0
```

iPhone の Safari で `http://<Mac の IP>:8770/` を開くと一覧が出ます。
Mac が起動している間だけ見られます。常時使うなら launchd に登録してください。
