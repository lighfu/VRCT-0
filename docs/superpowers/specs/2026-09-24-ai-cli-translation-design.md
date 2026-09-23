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
- タイムアウト・プロセス終了・プロトコル異常の場合は、セッションを閉じて `AiCliError` を投げる。次の `translate` で自動的に作り直す。起動か、起動したてのプロセスの最初のターンでの失敗には `startup` を付ける（AICliClient のサーキットブレーカーが見る）。
- アダプターがツール使用の兆しを見つけたら（`_interpret` が `("tool", …)` を返す）、`AiCliToolUseError`（`AiCliError` の子）を投げてプロセスを殺す。
- 起動時は作業フォルダとして `<PATH_LOCAL>/ai_cli_workspace`（空フォルダ、無ければ作る）を `cwd` にする。
- Windows ではコンソールを出さない（`CREATE_NO_WINDOW`）。
- 実行ファイルは `shutil.which` で探す。codex は npm のシムなので `codex.cmd` を優先する。

### 各アダプターのプロトコル

**claude**（`claude_session.py`）
- コマンド: `claude -p --input-format stream-json --output-format stream-json --verbose --model <model> --tools "" --no-session-persistence --setting-sources project --strict-mcp-config --system-prompt <基本指示>`
- 入力（1 行 1 JSON）: `{"type":"user","message":{"role":"user","content":"<prompt>"}}`
- 完了: `{"type":"result", ...}` 行。`is_error` が真なら失敗、偽なら `result` が応答テキスト。
- 起動完了は最初のターンの応答で判断する（`start()` は起動だけ行い、起動待ちは初回ターンのタイムアウトを 120 秒に延ばして吸収する）。
- 見張り: ターンごとに来る `{"type":"system","subtype":"init"}` の `tools` か `mcp_servers` が空でなければ、起動の失敗として扱う（起動引数が効いていない）。assistant の `tool_use` ブロックを見たら、そのターンを失敗にしてプロセスを作り直す。

**agy**（`agy_session.py`）
- コマンド: `agy --input-format stream-json --output-format stream-json --model <model> --agent vrct-translator --disable-slash-commands --print=`（`--print=` は空の値で付ける。`-p` を先に置くと次のフラグをプロンプトと解釈してしまう）
- 環境変数 `USERPROFILE`・`HOME` を VRCT 専用のホーム（`<PATH_LOCAL>/ai_cli_agy_home`）に向けて起動する。起動の前に、そのホームへ次を書く。
  - `.gemini/antigravity-cli/settings.json`: `toolPermission: "strict"`、`permissions.deny` に `command(*)`・`read_file(*)`・`write_file(*)`・`read_url(*)`・`execute_url(*)`・`mcp(*)`・`unsandboxed(*)`
  - `.gemini/config/agents/vrct-translator.md`: `tools: []`・`excludeDefaultComponents: true`・`inheritMcp: false`・`inheritCustomizations: false`・`subagent: false` のエージェント。本文（システムプロンプト）は基本指示。
- 入力: `{"event":"user","message":{"role":"user","content":"<prompt>"}}`
- 完了: `{"event":"result","result":{"status":"SUCCESS"|"ERROR","response":"...","error":"..."}}`。`status` が `SUCCESS` 以外は失敗。
- 見張り: `step_update` の `step_type` が `tool`（または `tool_info`・`subagent_info` がある）か、`result.denied_actions` がこのプロセスで前より増えていたら、そのターンを失敗にしてプロセスを作り直す（実物の agy は許可を求めるイベントを出さず、拒否したツールのターンを `SUCCESS`・`response: ""` で終える）。
- プロセスを止めたら、その会話 ID の保存物（`conversations/<id>.db`・`brain/<id>/`・`annotations/<id>.pbtxt`・`presence/<id>.lock`）だけを専用ホームから消す。ログは新しい 5 つだけ残す。

**codex**（`codex_session.py`）
- コマンド: `codex -c notify=[] -c web_search="disabled" -c history.persistence="none" -c project_doc_max_bytes=0 -c check_for_update_on_startup=false -c include_*_instructions=false … -c features.<名前>=false … app-server`（標準入出力で JSON-RPC、1 行 1 メッセージ）。切る機能は shell_tool・unified_exec・shell_snapshot・apps・plugins・remote_plugin・browser_use・browser_use_external・in_app_browser・computer_use・image_generation・view_image・multi_agent・goals・hooks・memories・tool_suggest・skill_search・sleep_tool・workspace_dependencies。
- 起動: `initialize`（`clientInfo: {name:"vrct", version:<VRCT の版>}`）→ 応答を待つ → `initialized` 通知 → `config/read`（`cwd`: 作業フォルダ）でユーザー設定の MCP サーバー名を読む → `thread/start`（`model`, `approvalPolicy:"never"`, `sandbox:"read-only"`, `baseInstructions:<基本指示>`, `ephemeral: true`, `config: {"mcp_servers": {<名前>: {"enabled": false}, …}}`）→ `result.thread.id` を保持。`-c mcp_servers={}` ではユーザーの MCP サーバーが消えない（設定が重ね合わされる）ので、名前ごとに止める。
- 1 ターン: `turn/start`（`threadId`, `input:[{"type":"text","text":"<prompt>"}]`, `effort:"low"`）。`item/completed` のうち `item.type` が `agentMessage` のものを集め、`turn/completed` で完了。訳文は `phase: "final_answer"` のもの（無ければ `commentary` 以外の最後のもの）。
- `error` 通知のうち `willRetry: true` のものは codex 自身が再試行するので無視する。
- 見張り: `userMessage`・`agentMessage`・`reasoning`・`contextCompaction` 以外の item（`commandExecution`・`mcpToolCall`・`webSearch`・`fileChange` など）か、サーバーからクライアントへの要求（承認要求など）が来たら、そのターンを失敗にしてプロセスを作り直す。

### catalog.py

- `detectInstalledTools() -> list[str]` — `["codex","claude","agy"]` のうち `shutil.which` で見つかるもの（この順）。
- `listModels(tool) -> list[str]`:
  - codex: `codex debug models` の JSON（`models[].slug`）。`review` を含む slug は除く。
  - agy: `agy models` の出力のうち「`<id>\t<表示名>`」の行の id。
  - claude: 固定で `["haiku", "sonnet", "opus"]`（一覧を返すコマンドが無いため）。
- 一覧取得は 30 秒でタイムアウトし、失敗したら空リストを返してエラーログを残す。`subprocess.run(timeout=…)` は `codex.cmd` の孫プロセス（node）を待ち続けるので、`Popen` + `communicate(timeout)` にして、締め切りを過ぎたら `taskkill /T` で木ごと止める。

### AICliClient（translation_ai_cli.py）

- 既存の LLM クライアントと同じメソッドを持つ: `getModelList()`, `getModel()`, `setModel(model) -> bool`, `updateClient()`, `setContextHistory(items)`, `translate(text, input_lang, output_lang) -> str`。
- 加えて: `getTool()`, `setTool(tool) -> bool`, `getInstalledTools()`, `checkConnection() -> bool`（選んだ CLI が入っていればセッションを起動しておき True）。
- プロンプト: `translation_ai_cli.yml` の `system_prompt` と `buildSystemPrompt`（履歴の注入を含む）で毎ターンの指示を組み立て、「指示 + 空行 + 原文」を 1 つのユーザーメッセージとして送る（入力・出力言語はターンごとに変わるので、セッションの基本指示には入れない）。セッションの基本指示は「翻訳だけを出力する」だけにする。
- セッションは CLI ごとに 1 本。CLI かモデルを変えたら、そのセッションを閉じる（次の翻訳か接続確認で作り直す）。
- `updateClient()` は、裏スレッドでセッションを起動し、短い確認のターン（「OK」を訳させる）を 1 回送る（claude の起動を最初の翻訳で待たないためと、未ログインなどを最初のメッセージより前に見つけるため）。既にターンを終えたセッションには送らない。
- モデル一覧は CLI ごとに覚える。問い合わせ直すのは接続確認と CLI の切り替えのときだけで、`setModel()` は覚えた一覧で確かめる（覚えていないときだけ 1 回問い合わせる）。問い合わせに失敗したら前に覚えた一覧を返す。
- サーキットブレーカー: 起動か、起動したてのプロセスの最初のターンが失敗したら（未ログイン・CLI の故障・応答しないなど）、60 秒は CLI を呼ばずに `translate()` を即座に失敗させる（呼び出し元は CTranslate2 に切り替わり、メッセージごとに CLI の起動を待たない）。60 秒を過ぎると、翻訳は失敗させたまま裏で確認のターンを送り、通れば元に戻す。接続確認が通ったときも閉じる。ツールを使おうとしたことによる失敗では開かない。状態が変わったら Controller に知らせ、`/run/ai_cli_connection` で UI の接続表示を合わせる（使えなくなったときは `CONNECTION_AI_CLI_FAILED` を送り、通知を出す）。
- 入力が空でないのに訳文が空なら、失敗（`AiCliError`）にする。
- 1 回の翻訳でターゲット言語が複数あると、セッションのロックでターンが 1 つずつ走る（3 言語なら待ち時間もおよそ 3 倍。agy では 10 秒を超えうる）。まとめて 1 ターンで訳すのは今後の課題。

## 設定・エンドポイント

エンジンキー: `AI_CLI`（`languages.yml` に `source/target: *openai_langs` で追加）。

`CONNECTION_PROVIDER_REGISTRY` に `AI_CLI` を追加する（疎通確認型。認証キーは持たない）。

config（`config.py`）:
- `SELECTABLE_AI_CLI_TOOL_LIST`（保存しない。起動時に検出）
- `SELECTED_AI_CLI_TOOL`（保存する。`codex`/`claude`/`agy` のいずれか。既定は `claude`。起動時に保存値の CLI が見つからなければ、検出された最初の CLI をその起動の間だけ使い、保存値は書き換えない）
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

- CLI の切り替え・接続確認・モデルの選択と、起動時の AI CLI の確認は、1 つのロックで順に実行する（`SELECTED_AI_CLI_MODEL` は「今選んでいる CLI」の欄に書くので、並行するとある CLI のモデルを別の CLI の欄に保存しうる）。
- 接続確認に失敗しても、CLI ごとに覚えたモデル（`SELECTED_AI_CLI_MODELS`）は消さない。UI にはモデル一覧 `[]` と選択モデル `None` を送る。
- 起動時: どのタブも AI CLI を使っていなければ、CLI が入っているかだけを確かめ、モデル一覧の取得は起動の後に裏で行って UI に送る（起動を遅らせない）。使っているときは起動中に 1 回だけ取り、セッションを裏で起動する。

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

翻訳する文と履歴には他人の発言（スピーカーの文字起こし）が入り、訳文は VRChat のチャットボックスに送られる。CLI への入力は信用できないものとして扱う。

**起動引数で保証すること**（2026-09-24、codex 0.155.1 / Claude Code 2.1.281 / agy 1.2.9 の実機で確認）
- claude: `--tools ""`・`--strict-mcp-config`・`--setting-sources project` で、`system/init` の `tools` と `mcp_servers` が両方とも空になる（claude.ai のコネクターも出ない）。`--no-session-persistence` で会話を保存しない。
- codex: `-c` で notify・Web 検索・シェル・アプリ/プラグイン/ブラウザー/コンピューター操作・画像・サブエージェント・フック・メモリを切り、ユーザーの MCP サーバーはスレッドの設定で止める。実機で、ユーザーの MCP サーバー（codegraph）・cua_node/node_repl のランタイム・通知プログラムが起動しなくなった。スレッドは `ephemeral` で、`~/.codex/sessions` にもスレッドの履歴 DB にも翻訳した発話が残らない（デバッグ用ログは下の「最善を尽くすだけのこと」を参照）。
- agy: 専用のホームで起動するので、ユーザーの MCP サーバー・許可ルール（例: `mcp(unity-agent/…)` の常時許可）・GEMINI.md を読まず、会話もユーザーの履歴に残らない。ツールの無いエージェントを選ぶと、モデルが使えるツールは `manage_task`（自分のバックグラウンド作業の一覧・停止）だけになる。さらに settings.json の deny ルールで、ファイル・コマンド・Web・MCP を拒否する（エージェントを使わない agy でも、作業フォルダ外のファイル読み取り・URL 取得・コマンドが拒否されることを確認）。

**実行時の見張り**（起動引数が効かなかった場合の保険）
- ツールを使おうとした兆し（agy の tool の `step_update`・`denied_actions` の増加、codex のツール系の item・サーバーからの要求、claude の `tool_use`）を見たら、そのターンを失敗にしてプロセスを殺し、指示が紛れ込んだ会話ごと捨てる。そのメッセージは CTranslate2 で訳される。
- 見張りはツールの実行を止めるものではない（agy の書き込みは 0.03 秒で終わる）。止めるのは起動引数の役目で、見張りはツールの結果が訳文として返るのと、汚れた会話が続くのを防ぐ。

**最善を尽くすだけのこと**
- 履歴は `<conversation_context>` で囲み、指示ではなくデータだと明記するが、モデルが従わない保証はない（ツールが無いので、できるのは訳文をゆがめることだけ）。
- codex はユーザーの `~/.codex/AGENTS.md` を設定で外せない（`project_doc_max_bytes=0` で外れるのは作業フォルダ側の AGENTS.md だけ）。その指示が翻訳に混ざる可能性が残る。
- codex は、送ったターンの本文（翻訳する文と履歴）を自分のデバッグ用ログ `~/.codex/logs_2.sqlite` に DEBUG で記録する（codex が一定期間で消す。この PC では 10 日ほど前からの記録が残っていた。codex の `/feedback` で送られるログにも入りうる）。`-c sqlite_home=<VRCT のフォルダ>` で移せるが、初回の起動に 44 秒かかり、ユーザーの codex のスレッドの一覧（題名・最初のメッセージ）を移した先にコピーするので採らない。UI の説明文（`ai_cli_tool.desc`）で知らせる。
- 環境変数 `ANTHROPIC_API_KEY` があると、claude はログイン中のサブスクリプションではなくその API キーで課金する（VRCT は消さない。それで使っている人もいるため）。
- agy の専用ホームには、プロセスを止めたときに消し切れなかった会話（強制終了など）と、agy が書く会話の要約（題名など）が残りうる。ユーザーの agy の履歴には残らない。

その他:
- CLI は専用の空フォルダで動かし、リポジトリやユーザーのファイルを作業対象にしない。
- 送るのは翻訳対象の文と履歴（既存の LLM エンジンと同じ内容）だけ。

## テスト

- 各アダプター: プロトコルをまねる偽 CLI（`src-python/test/fixtures/fake_ai_cli.py`、引数で claude / agy / codex の振る舞いを切り替える Python スクリプト）を起動して、次を確かめる。
  - 起動と 1 ターンの翻訳
  - 複数ターン
  - 50 ターンでの作り直し
  - タイムアウト時にプロセスを終了させ `AiCliError` を投げ、次のターンで作り直す
  - プロセスの異常終了
  - ツールを使おうとしたら失敗扱いにしてプロセスを作り直す（agy の tool の step_update・denied_actions の増加、codex の commandExecution・サーバーからの要求、claude の tool_use。偽 CLI のイベントの形は実機で記録したものに合わせる）
  - claude の `system/init` にツールや MCP サーバーがあれば起動の失敗にする
  - agy の専用ホーム（環境変数・settings.json・エージェント）と、止めたプロセスの会話だけを消すこと
  - codex の JSON-RPC 手順（initialize → config/read → thread/start → turn/start）、`ephemeral`、MCP サーバーの停止、`willRetry` の error、途中経過のメッセージを訳文に混ぜないこと
- catalog: `shutil.which` と `_run` をモックして、検出と各 CLI のモデル一覧の解析を確かめる。`.cmd` のシムが孫プロセスを起こす場合でも締め切りで木ごと止まることを、実際のプロセスで確かめる。
- AICliClient: セッションをモックして、プロンプトの組み立て、CLI・モデルの切り替え時のセッション破棄、モデル一覧の記憶、サーキットブレーカー、確認のターン、空の訳文を確かめる。
- 設定・エンドポイント・レジストリ: 既存の `test_translation_providers.py` と `test_ui_endpoint_contract.py` が新エントリで通ること、新規エンドポイントのテストを追加する。
- 実 CLI での確認は最後に手動（各アカウントの利用枠を少し使う）: 3 つの CLI それぞれで、画面からチャットを送って翻訳されること。

## 範囲外

- 音声認識（文字起こし）への CLI の利用。
- CLI のインストールやログインをアプリから行うこと（見つからない・未ログインはエラーで知らせるだけ）。
- ストリーミング表示（応答を逐次表示する）。
