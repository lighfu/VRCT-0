# AI CLI 翻訳プロバイダー — 設計

- 日付: 2026-09-24
- ブランチ: `feat/ai-cli-translation`
- 位置づけ: 翻訳エンジンの追加（ロードマップ「翻訳エンジンの追加（AI による翻訳等）」）

## 目的

ユーザーの PC に入っている AI の CLI（OpenAI Codex CLI `codex`、Claude Code `claude`、Google Antigravity CLI `agy`）を使って翻訳できるようにする。
API キーを別に用意しなくても、各 CLI にログイン済みのアカウントで翻訳できることが狙い。
翻訳エンジンの一覧に「AI CLI」を 1 つ追加し、設定で CLI とそのモデルを選ぶ。

**成功基準**
- 3 つの CLI それぞれで、チャット送信の翻訳が動く。
- CLI 起動済み（常駐）の状態では、1 件あたりの待ち時間が claude で数秒以内、agy / codex で 10 秒以内に収まる。
- CLI が入っていない・ログインしていない・応答しない場合も、アプリは落ちずにエラーを返し、次の翻訳で自動的に立ち直る。

## 事前検証（2026-09-24、この PC）

| CLI | 版 | 毎回起動 | 常駐: 起動 | 常駐: 2 回目以降 |
|---|---|---|---|---|
| claude | 2.1.280 | 24〜104 秒 | 31 秒 | 1.5 秒 |
| agy | 1.2.9 | 10 秒 | 約 4 秒 | 約 3.8 秒 |
| codex | 0.155.1 | 13 秒 | 約 5 秒 | 6.7〜9 秒（gpt-5.5） |

- claude はユーザー設定（フック・プラグイン・MCP）を読むと 1 ターン 17 秒以上かかる。`--setting-sources project --strict-mcp-config` で読ませないと速い。`--bare` はログイン情報も読まないので使えない。
- 毎回起動では遅すぎるので、**常駐方式**を採る。

## 構成

```
src-python/models/translation/
  translation_ai_cli.py          # AICliClient（翻訳エンジンとしての入口）
  ai_cli/
    __init__.py
    base.py                      # CliSession 基底（起動・1 ターン・終了・タイムアウト・作り直し）
    claude_session.py            # claude の stream-json アダプター
    agy_session.py               # agy の stream-json アダプター
    codex_session.py             # codex app-server (JSON-RPC) アダプター
    catalog.py                   # 導入済み CLI の検出とモデル一覧の取得
  translation_settings/prompt/translation_ai_cli.yml
```

### CliSession（base.py）

各アダプターは同じインターフェースを持つ:

- `start(model: str) -> None` — プロセスを起動し、翻訳を受け付けられる状態まで待つ（起動タイムアウト 120 秒）。
- `translate(prompt: str, timeout: float = 60.0) -> str` — 1 ターン送り、応答テキストを返す。
- `close() -> None` — プロセスを終了させる（子プロセスごと）。
- `isAlive() -> bool`

共通の振る舞い:
- 1 セッション内のターンはロックで直列化する。
- ターン数が 50 に達したら、次のターンの前に作り直す（会話の蓄積によるトークン増と遅延を抑える）。
- タイムアウト・プロセス終了・プロトコル異常の場合は、セッションを閉じて `AiCliError` を投げる。次の `translate` で自動的に作り直す。
- 起動時は作業フォルダとして `<PATH_LOCAL>/ai_cli_workspace`（空フォルダ、無ければ作る）を `cwd` にする。
- Windows ではコンソールを出さない（`CREATE_NO_WINDOW`）。
- 実行ファイルは `shutil.which` で探す。codex は npm のシムなので `codex.cmd` を優先する。

### 各アダプターのプロトコル

**claude**（`claude_session.py`）
- コマンド: `claude -p --input-format stream-json --output-format stream-json --verbose --model <model> --tools "" --no-session-persistence --setting-sources project --strict-mcp-config --system-prompt <基本指示>`
- 入力（1 行 1 JSON）: `{"type":"user","message":{"role":"user","content":"<prompt>"}}`
- 完了: `{"type":"result", ...}` 行。`is_error` が真なら失敗、偽なら `result` が応答テキスト。
- 起動完了は最初のターンの応答で判断する（`start()` は起動だけ行い、起動待ちは初回ターンのタイムアウトを 120 秒に延ばして吸収する）。

**agy**（`agy_session.py`）
- コマンド: `agy --input-format stream-json --output-format stream-json --model <model> --print=`（`--print=` は空の値で付ける。`-p` を先に置くと次のフラグをプロンプトと解釈してしまう）
- 入力: `{"event":"user","message":{"role":"user","content":"<prompt>"}}`
- 完了: `{"event":"result","result":{"status":"SUCCESS"|"ERROR","response":"...","error":"..."}}`。`status` が `SUCCESS` 以外は失敗。
- ツールの使用許可を求めるイベント（`ask_permission` / `ask_custom_permission` など）が来たら、許可せずにそのターンを失敗扱いにしてセッションを作り直す。

**codex**（`codex_session.py`）
- コマンド: `codex app-server`（標準入出力で JSON-RPC、1 行 1 メッセージ）
- 起動: `initialize`（`clientInfo: {name:"vrct", version:<VRCT の版>}`）→ 応答を待つ → `initialized` 通知 → `thread/start`（`model`, `approvalPolicy:"never"`, `sandbox:"read-only"`, `baseInstructions:<基本指示>`）→ `result.thread.id` を保持。
- 1 ターン: `turn/start`（`threadId`, `input:[{"type":"text","text":"<prompt>"}]`）。`item/completed` のうち `item.type` が `agentMessage` のものの `text` を集め、`turn/completed` で完了。
- 推論の強さを下げるパラメータ（`effort` など）が `turn/start` / `thread/start` にあれば `low` を指定する。無ければ指定しない（実装時に codex 0.155 の app-server で確かめる）。

### catalog.py

- `detectInstalledTools() -> list[str]` — `["codex","claude","agy"]` のうち `shutil.which` で見つかるもの（この順）。
- `listModels(tool) -> list[str]`:
  - codex: `codex debug models` の JSON（`models[].slug`）。`review` を含む slug は除く。
  - agy: `agy models` の出力のうち「`<id>\t<表示名>`」の行の id。
  - claude: 固定で `["haiku", "sonnet", "opus"]`（一覧を返すコマンドが無いため）。
- 一覧取得は 30 秒でタイムアウトし、失敗したら空リストを返してエラーログを残す。

### AICliClient（translation_ai_cli.py）

- 既存の LLM クライアントと同じメソッドを持つ: `getModelList()`, `getModel()`, `setModel(model) -> bool`, `updateClient()`, `setContextHistory(items)`, `translate(text, input_lang, output_lang) -> str`。
- 加えて: `getTool()`, `setTool(tool) -> bool`, `getInstalledTools()`, `checkConnection() -> bool`（選んだ CLI が入っていればセッションを起動しておき True）。
- プロンプト: `translation_ai_cli.yml` の `system_prompt` と `buildSystemPrompt`（履歴の注入を含む）で毎ターンの指示を組み立て、「指示 + 空行 + 原文」を 1 つのユーザーメッセージとして送る（入力・出力言語はターンごとに変わるので、セッションの基本指示には入れない）。セッションの基本指示は「翻訳だけを出力する」だけにする。
- セッションは CLI ごとに 1 本。CLI かモデルを変えたら、そのセッションを閉じる（次の翻訳か接続確認で作り直す）。
- `updateClient()` は、裏スレッドでセッションを起動しておく（claude の起動 31 秒を最初の翻訳で待たないため）。

## 設定・エンドポイント

エンジンキー: `AI_CLI`（`languages.yml` に `source/target: *openai_langs` で追加）。

`CONNECTION_PROVIDER_REGISTRY` に `AI_CLI` を追加する（疎通確認型。認証キーは持たない）。

config（`config.py`）:
- `SELECTABLE_AI_CLI_TOOL_LIST`（保存しない。起動時に検出）
- `SELECTED_AI_CLI_TOOL`（保存する。`codex`/`claude`/`agy` のいずれか。既定は検出された最初の CLI、無ければ `claude`）
- `SELECTABLE_AI_CLI_MODEL_LIST`（保存しない。選んだ CLI のモデル一覧）
- `SELECTED_AI_CLI_MODELS`（保存する。`{tool: model}`。既定 `{"claude":"haiku","codex":"","agy":""}`）
- `SELECTED_AI_CLI_MODEL`（保存しない算出値。`SELECTED_AI_CLI_MODELS[SELECTED_AI_CLI_TOOL]`）

エンドポイント（mainloop.py）:
- `/get/data/selectable_ai_cli_tool_list`
- `/get/data/selected_ai_cli_tool`、`/set/data/selected_ai_cli_tool`（変更時にモデル一覧を取り直して `/run/selectable_ai_cli_model_list` を送る）
- `/get/data/selectable_ai_cli_model_list`
- `/get/data/selected_ai_cli_model`、`/set/data/selected_ai_cli_model`
- `/run/ai_cli_connection`（接続確認。結果で `SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"]` を更新）
- `/get/data/connected_ai_cli`

エラーコード（errors.py）:
- `CONNECTION_AI_CLI_FAILED` — 選んだ CLI が見つからない / セッションを起動できない
- `MODEL_AI_CLI_INVALID` — モデル一覧に無いモデルを指定した
- 翻訳時の失敗（タイムアウト・未ログイン等）は、既存の翻訳失敗と同じ経路（`translate()` が例外 → `Translator.translate()` が False を返す → 既存の失敗時処理）に乗せる。未ログインなど原因がわかる場合はエラーログに CLI の出力を残す。

## UI

翻訳の設定画面（`Translation.jsx`）に、Ollama と同じ並びで次を追加する:
- AI CLI の選択（検出された CLI だけを選択肢に出す）
- モデルの選択
- 接続確認ボタン

`ui_config_setter.js` の宣言、`useReceiveRoutes.js`（接続状態）、`useLLMConnection.js`、`store.js`、`ui_configs.js`（エンジン一覧に `AI_CLI`、表示名「AI CLI」）、`_useBackendErrorHandling.js`（2 つのエラーコード）、`locales/*.yml` を更新する。文言は ja / en、他言語は en を入れる。

## 安全性

- CLI は専用の空フォルダで動かし、リポジトリやユーザーのファイルを作業対象にしない。
- claude はツールなし（`--tools ""`）、codex は読み取り専用で承認なし、agy はツール許可を与えない。
- 送るのは翻訳対象の文と履歴（既存の LLM エンジンと同じ内容）だけ。

## テスト

- 各アダプター: プロトコルをまねる偽 CLI（`src-python/test/fixtures/fake_ai_cli.py`、引数で claude / agy / codex の振る舞いを切り替える Python スクリプト）を起動して、次を確かめる。
  - 起動と 1 ターンの翻訳
  - 複数ターン
  - 50 ターンでの作り直し
  - タイムアウト時にプロセスを終了させ `AiCliError` を投げ、次のターンで作り直す
  - プロセスの異常終了
  - agy の許可要求イベントで失敗扱いにする
  - codex の JSON-RPC 手順（initialize → thread/start → turn/start）
- catalog: `shutil.which` と `subprocess.run` をモックして、検出と各 CLI のモデル一覧の解析を確かめる。
- AICliClient: セッションをモックして、プロンプトの組み立て、CLI・モデルの切り替え時のセッション破棄、`checkConnection` を確かめる。
- 設定・エンドポイント・レジストリ: 既存の `test_translation_providers.py` と `test_ui_endpoint_contract.py` が新エントリで通ること、新規エンドポイントのテストを追加する。
- 実 CLI での確認は最後に手動（各アカウントの利用枠を少し使う）: 3 つの CLI それぞれで、画面からチャットを送って翻訳されること。

## 範囲外

- 音声認識（文字起こし）への CLI の利用。
- CLI のインストールやログインをアプリから行うこと（見つからない・未ログインはエラーで知らせるだけ）。
- ストリーミング表示（応答を逐次表示する）。
