# readpaper-workspace

Chrome で論文のページを開いてツールバーの **DP** ボタンを1回押すと、PDF を落として
AI エージェント（**Claude Code** または **Codex CLI**）に読ませ、数式つきの HTML 解説を
自動生成し、macOS の通知で知らせる — そのための一式です。

```
browser click ──▶ Chrome 拡張 ──▶ native host ──▶ (detached) worker
                  ページから        PDF url と        DL → %PDF 検証 → 図表クロップ
                  PDF url を発見    論文 id を解決     → エージェント → 索引再生成 → 通知
```

対応 venue: arXiv / OpenReview / PMLR / JMLR / ACL Anthology / NeurIPS / CVF、
および任意の `*.pdf` URL。

## → セットアップは **[SETUP.md](SETUP.md)**

新しい Mac にゼロから入れる手順（Homebrew / poppler / Python / エージェント CLI /
拡張のロード / トラブルシュート）はすべてそこにあります。所要 15〜30 分。

## このリポジトリに入っているもの

| パス | 中身 |
|---|---|
| `tools/readpaper/` | Chrome 拡張・native messaging host・worker・図表抽出 |
| `skills/explain-via-html/` | HTML 解説の書き方の規約（外部スキルの pin 同梱。経緯は `skills/README.md`） |
| `CLAUDE.md` / `AGENTS.md` | プロジェクト固有の追加ルール（数式の書き方、図の検証、Q&A 枠） |
| `build_index.py` | `html-expl/index.html`（解説の一覧ページ）を生成 |
| `SETUP.md` | セットアップ手順 |

**入っていないもの**: ダウンロードした論文 (`papers/`)、生成された解説 (`html-expl/`)、
ログ、個人的なノート。clone した直後は空の作業場で、使うにつれてローカルに溜まります。

## 動作環境

macOS + Google Chrome + 有料のエージェントアカウント（Codex または Claude Code）。
論文1本の生成で 20万〜43万トークン規模を消費します。

## 安全上の注意

worker はエージェントを**承認プロンプトなし・サンドボックスなし**で起動します。
つまりダウンロードした PDF の中身を、確認なしにファイル操作やコマンド実行ができる
エージェントに読ませています。細工された PDF による prompt injection が理論上あり得るため、
**素性の分かる学術サイトの論文にだけ使ってください。** 詳細は SETUP.md §10。
