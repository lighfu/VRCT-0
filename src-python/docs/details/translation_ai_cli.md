# translation_ai_cli.py / ai_cli/ - AI CLI 翻訳クライアント

## 概要

ユーザーの PC に入っている AI の CLI（OpenAI Codex CLI `codex`、Claude Code `claude`、Google Antigravity CLI `agy`）を常駐させ、翻訳エンジン「AI CLI」（エンジンキー `AI_CLI`）として使う。API キーは要らず、各 CLI にログイン済みのアカウントで翻訳する。

設計の詳細と実機での確認結果は `docs/superpowers/specs/2026-09-24-ai-cli-translation-design.md` と `docs/perf/README.md`（「追記（AI CLI 翻訳）」）にある。

## ファイル

| ファイル | 役割 |
|---|---|
| `translation_ai_cli.py` | `AICliClient`。翻訳エンジンとしての入口（CLI・モデルの選択、プロンプトの組み立て、モデル一覧の記憶、サーキットブレーカー、確認のターン） |
| `ai_cli/base.py` | `CliSession`（常駐プロセスの起動・1 ターン・終了・タイムアウト・作り直し）、`AiCliError`、`AiCliToolUseError` |
| `ai_cli/claude_session.py` | claude の stream-json アダプター |
| `ai_cli/agy_session.py` | agy の stream-json アダプター（専用ホーム・ツールの無いエージェント） |
| `ai_cli/codex_session.py` | codex app-server（JSON-RPC）アダプター |
| `ai_cli/catalog.py` | 導入済み CLI の検出とモデル一覧の取得 |
| `translation_settings/prompt/translation_ai_cli.yml` | 毎ターンの指示と履歴のテンプレート |

## 公開 API

```python
class AICliClient:
    def __init__(root_path: str = None, workspace: str = None, client_version: str = "")
    def getInstalledTools() -> list[str]
    def getTool() -> str | None
    def setTool(tool: str) -> bool            # 入っていない CLI は False。CLI が変わったらセッションを閉じる
    def getModelList() -> list[str]           # CLI に問い合わせ直して CLI ごとに覚える
    def getModel() -> str | None
    def setModel(model: str) -> bool          # 覚えた一覧で確かめる
    def authenticationCheck() -> bool         # CLI の実行ファイルがあるか
    def updateClient() -> None                # 裏でセッションを起動し、確認のターンを 1 回送る
    def setContextHistory(items: list[dict]) -> None
    def translate(text: str, input_lang: str, output_lang: str) -> str
    def setStatusCallback(callback) -> None   # 使えなくなった/立ち直ったときに bool で呼ぶ
    def isAvailable() -> bool
    def resetBreaker() -> None                # 接続確認が通ったときに呼ぶ
    def close() -> None                       # 今のセッションを終える（再起動可能）
    def shutdown() -> None                    # クライアントを恒久的に終える
```

Translator 経由の呼び出し（`model.py`）: `authenticationTranslatorAiCli`・`setTranslatorAiCliTool`・`getTranslatorAiCliModelList`・`setTranslatorAiCliModel`・`updateTranslatorAiCliClient`・`closeTranslatorAiCli`・`setTranslatorAiCliStatusCallback`。

## 振る舞い

- **常駐**: CLI ごとに 1 本のプロセスを起動したままにし、1 行 1 JSON でターンを送る。1 セッション 50 ターンで作り直す。最初のターンは 120 秒、以降は 60 秒でタイムアウト。
- **モデル一覧**: `codex debug models` / `agy models` は数秒かかるので、CLI ごとに覚える。問い合わせ直すのは接続確認と CLI の切り替えのとき。claude は固定で `haiku` / `sonnet` / `opus`。取得は 30 秒で打ち切り、`.cmd` のシムの孫プロセスまで止める。
- **確認のターン**: `updateClient()` は「OK」を訳させる短いターンを送る。未ログインなどはここで見つかる（実機で claude の未ログインは約 2 秒で検出）。最初のターンの起動待ちも裏で済むので、最初の翻訳が速くなる。
- **サーキットブレーカー**: 起動か最初のターンが失敗したら 60 秒は CLI を呼ばずに即座に失敗させる（CTranslate2 に切り替わる）。時間が過ぎたら裏で確認のターンを送って立ち直りを確かめる。状態が変わると Controller が `/run/ai_cli_connection` で UI に知らせる。
- **空の訳文**: 入力が空でないのに訳文が空なら失敗にする。
- **複数のターゲット言語**: セッションのロックでターンが 1 つずつ走るので、3 言語ならおよそ 3 倍待つ。

## 安全性（要点）

CLI への入力には他人の発言が入るので信用しない。

| CLI | 起動引数・環境で止めるもの | 実行時の見張り |
|---|---|---|
| claude | `--tools ""`・`--strict-mcp-config`・`--setting-sources project`・`--no-session-persistence` | `system/init` にツールか MCP があれば起動失敗、`tool_use` でターン失敗 |
| codex | `-c` で notify・Web 検索・シェル・プラグインなどの機能を切る、MCP サーバーはスレッドの設定で止める、`ephemeral` スレッド | 会話以外の item・サーバーからの要求でターン失敗 |
| agy | 専用ホーム（`<PATH_DATA>/ai_cli_agy_home`）、ツールの無いエージェント（`--agent vrct-translator`）、strict と deny ルール | tool の `step_update`・`denied_actions` の増加でターン失敗 |

ターンが失敗するとプロセスを殺し、会話ごと捨てる（`AiCliToolUseError`。ブレーカーは開かない）。残る注意点（codex の `~/.codex/AGENTS.md`、`ANTHROPIC_API_KEY`、履歴の囲みは最善を尽くすだけ）は設計書の「安全性」を参照。

## 保存されるもの

- claude: 会話を保存しない。
- codex: `ephemeral` スレッドなので、ユーザーの `~/.codex/sessions` やスレッドの履歴 DB に翻訳した発話は残らない。ただし codex のデバッグ用ログ（`~/.codex/logs_2.sqlite`）には送ったターンの本文が記録され、codex が一定期間で消す。移す方法（`sqlite_home`）はユーザーのスレッドの一覧をコピーするので採らず、UI の説明文で知らせている。
- agy: ユーザーの `~/.gemini/antigravity-cli` には何も残らない。VRCT 専用のホームに会話が保存されるが、プロセスを止めたときにその会話 ID のファイルを消す。ログは新しい 5 つだけ残す。

## 関連

- `details/translation_translator.md`
- `details/translation_prompt_history.md`
- `controller.py`: `checkTranslatorAiCliConnection`・`setSelectedAiCliTool`・`setTranslatorAiCliModel`（1 つのロックで順に実行）、`_checkAiCliAtStartup`・`_listAiCliModelsInBackground`・`_onAiCliStatusChange`
