# A-1: Python サイドカーのスリム化 — 設計

- 日付: 2026-09-23
- ブランチ: `perf/slim-sidecar`
- 位置づけ: 軽量化ロードマップ「A → B」の最初のサブプロジェクト

## 背景と目的

VRCT-0 は Tauri 2（薄い Rust シェル）+ React UI + Python サイドカー（PyInstaller onedir、stdin/stdout JSON 通信）の構成。
シェルと UI は既に軽量で、重さの大半は Python サイドカーの依存に由来する。

| 要因 | 実測 wheel サイズ | 用途 |
|---|---|---|
| nvidia-cublas-cu12 + nvidia-cudnn-cu12（CUDA 版のみ） | 541 MB + 683 MB | ctranslate2 の GPU 実行 |
| SudachiDict-full | 辞書 zip 127 MB（展開後はさらに大） | 読みがな表示（`transliteration_transliterator.py` の `dict_type="full"`） |
| transformers | 8.6 MB（import が重い） | CTranslate2 翻訳（M2M100 / NLLB）のトークナイザのみ |
| langchain-openai / -ollama / -google-genai（+ grpcio 等） | 依存多数 | LLM 翻訳。使っているのは `.invoke(messages)` だけ |

**成功基準**（3 指標を同等に扱う）: 配布サイズ・起動時間・常駐メモリの各基準値に対し、機能を落とさずに改善量を数字で示す。
いずれの指標も悪化させない（誤差範囲 ±5% を超える悪化は不可）。

## 範囲

### 含む
1. 計測ツールと基準値の記録
2. langchain 系の撤去
3. transformers の撤去
4. Sudachi 辞書: core 同梱 + full 後入れ
5. 重いモジュールの import 遅延化
6. 未使用依存の掃除（Python / JS）
7. CPU 版・CUDA 版の単一ビルド化（CUDA DLL を外部フォルダから読めるようにする）

### 含まない（別サブプロジェクト）
- CUDA ライブラリのダウンロード UI・更新処理 → インストーラーのサブプロジェクト
- PyInstaller の置き換え（Nuitka・組み込み Python + uv 等） → インストーラーのサブプロジェクト
- 常駐処理の Rust 移行 → B

## 進め方

計測駆動で、依存を 1 つずつ外す。各項目は独立したコミットにし、前後で §1 の計測を回して差分を記録する。
順序: §1 → §6 → §2 → §3 → §4 → §5 → §7 → 最終計測。
（掃除と置き換えで依存グラフが確定してから遅延 import と単一ビルドに手を付ける。）

## §1 計測

`tools/measure_footprint.py` を新設する。出力は JSON（`docs/perf/<label>-<date>.json`）。

- **配布サイズ**: `src-tauri/bin` の合計バイト数、直下ディレクトリ上位 20 件の内訳、NSIS インストーラー（存在する場合）のサイズ。
- **起動時間**: サイドカー exe を起動し、stdout に `/run/initialization_complete` の応答が出るまでの経過時間。5 回計測の中央値。
  サイドカーは watchdog で UI からの `/run/feed_watchdog` を待つので、計測ツールは UI の代わりに 20 秒間隔で送る。
  加えて開発環境で `python -X importtime src-python/mainloop.py` の結果を集計し、累積時間上位 30 モジュールを記録する。
- **常駐メモリ**: 起動完了から 30 秒後のサイドカープロセス RSS（子プロセス含む）。
  追加で、ローカル Whisper（small）と CTranslate2（m2m100_418M）を読み込んだ後の RSS も記録する。モデル未取得の環境ではこの項目を `null` にする。

基準値は CPU 版・CUDA 版それぞれで取得し `docs/perf/baseline-2026-09-23.json` として保存する。

## §2 langchain の撤去

- OpenAI 互換のクライアント（OpenAI / Groq / OpenRouter / LM Studio / PLaMo / Ollama）は `openai` SDK の `client.chat.completions.create(model=..., messages=...)` を直接呼ぶ。
  Ollama は `base_url=<ollama>/v1` の OpenAI 互換エンドポイントを使う。モデル一覧・疎通確認の既存 `requests` 実装はそのまま。
- Gemini は既に依存している `google-genai` の `client.models.generate_content` を直接使う。
- 共通処理（system プロンプトの組み立て、履歴の注入、応答テキストの取り出し）は各クライアントに重複しているので、`translation_llm_common.py` に 1 か所へまとめる。
  応答の取り出しは現行と同じ規則（文字列ならそのまま、リストなら文字列要素と `content` を連結、最後に `strip()`）。
- `requirements.txt` から `langchain-openai` / `langchain-ollama` / `langchain-google-genai` を外し、`openai` を明示的に追加する。
  `grpcio` は langchain-google-genai 経由でしか使っていなければ外す（`pip show` の Required-by で確認）。
- 例外の型が変わるため、呼び出し側（`translation_translator.py` のエラー処理）で捕捉している例外を確認し、同じエラーコードに落ちるようにする。

## §3 transformers の撤去

- `sentencepiece`（既存依存）で M2M100 / NLLB のトークナイザを自前実装する `translation_ct2_tokenizer.py` を新設する。
  - NLLB: `sentencepiece.bpe.model` でエンコードし、先頭にソース言語トークン、末尾に `</s>` を付ける。target_prefix は言語コード（現行と同じ）。
  - M2M100: `sentencepiece.bpe.model` + `vocab.json` を使い、`__<lang>__` 形式の言語トークンを付ける。target_prefix は `__<lang>__`（現行の `lang_code_to_token` と同じ値）。
  - デコード: 生成トークンから特殊トークンを除き、sentencepiece でデコードする。
- トークナイザファイルは既存の保存先（`weights/ctranslate2/<dir>/tokenizer/`）を再利用する。既存ユーザーの HF キャッシュ形式（`models--facebook--*/snapshots/<rev>/`）の中からファイルを探す。
  見つからない場合は `huggingface_hub.hf_hub_download` で必要なファイルだけ取得する（`huggingface_hub` は既存依存）。
- `requirements.txt` から `transformers` を外す。`tokenizers` 等の推移依存は faster-whisper が必要とする分だけ残る。

## §4 Sudachi 辞書

- 同梱するのは `SudachiDict-core` のみ。`requirements.txt` から `SudachiDict-full` を外す。
- 設定に「読みがなの高精度辞書（full）」を追加する（既定オフ）。オンにすると full 辞書を `weights/sudachi/` にダウンロードし、Whisper モデルと同じ進捗表示・エラー表示の流れで扱う。
- `Transliterator` は、full が設定オン・かつファイルが揃っていれば `system_dict=<path>` で full を、そうでなければ core を読む。
  full の読み込みに失敗したら core に戻してエラーログを残す。
- UI 側は既存の設定ページ部品（ダウンロード付きトグル）を流用する。文言は ja / en を追加し、他言語は en で埋める。

## §5 import の遅延化

- §1 の `-X importtime` 上位のうち、起動直後に必要ないものを使用直前の import に移す。
  候補: ctranslate2、faster_whisper、rapidocr、cv2、openvr / OpenGL / glfw、sudachipy、google.genai、openai、deepl、translators。
- 実際に移す対象は計測結果で決める（累積 50 ms 以上、かつ起動時に使わないもの）。
- 既存コードには `try: import X except: X = None` で有無を判定している箇所がある（`translation_utils.py` など）。
  可否判定は `importlib.util.find_spec` に置き換え、本体の import は使用時まで遅らせる。
- PyInstaller は静的解析で収集するので、遅延 import したモジュールが `hiddenimports` から漏れないことをビルド後に確認する。

## §6 未使用依存の掃除

- Python: `requirements.txt` の各パッケージについて、`src-python` からの直接 import と、他パッケージからの推移依存（`pip show` の Required-by）の両方で使われていないものを外す。
  直接 import が見当たらない候補（cloudscraper、exejs、niquests、aiohttp）は `translators` の依存かどうかを確認してから判断する。推移依存なら残す。
- JS: `@babel/standalone`、`jszip`、`semver` が `src-ui`・`locales`・`vite.config.js` のどこからも使われていなければ `package.json` から外す。

## §7 単一ビルド化

- `utils.py` の CUDA DLL 登録処理を拡張し、同梱の `nvidia/*/bin` に加えて外部フォルダ `%LOCALAPPDATA%\VRCT\cuda\bin`（存在すれば）も DLL 検索パスと `PATH` に追加する。
- `_CUBLAS_LIBRARY_NAME` による GPU 利用可否の判定は、外部フォルダの DLL も対象にする。
- `backend_cuda.spec` と `build_cuda.bat`、`npm run *-cuda` 系スクリプトは、インストーラーのサブプロジェクトで CUDA の取得処理ができるまで残す。
  A-1 での完了条件は「CPU 版ビルドに外部フォルダの CUDA DLL を置くと GPU で動く」ことまでとする。
- 既存のアプリ内更新（`controller.py` の CUDA 版への更新スレッド `th_start_update_cuda_software`）は A-1 では変更しない。インストーラーのサブプロジェクトで取得処理を設計するときの出発点にする。

## エラー処理の方針

- 置き換えた部分は、ユーザーに見えるエラーコード（`ErrorCode`）とメッセージが現行と同じになるようにする。
- full 辞書・CUDA DLL など後から入れる部品が欠けている・壊れている場合は、落ちずに標準構成（core 辞書・CPU）に戻り、エラーログを残す。

## テスト

- 既存 pytest をすべて通す（各コミットで実行）。
- §2: 各 LLM クライアントについて、SDK 呼び出しをモックし、送る messages と応答の取り出し結果が現行と一致するテストを追加する。
- §3: 照合テストを追加する。transformers 版と自前版で、ja / en / ko / zh / fr / de の各 5 文についてエンコード結果のトークン列とデコード結果が一致すること。
  照合には開発環境の transformers を使い、`requirements-dev.txt` にだけ残す。トークナイザファイルが無い環境ではスキップする。
- §4: core / full / full 破損の 3 パターンで `Transliterator` が初期化でき、代表文の読みが得られること。
- §7: 外部フォルダに DLL がある場合・無い場合で DLL 検索パスの登録と GPU 可否判定が正しいこと（ファイルシステムをモック）。
- 最終確認: CPU 版をビルドして実機で起動し、マイク文字起こし・API 翻訳・CTranslate2 翻訳・読みがな・OCR を一通り動かす。

## 成果物

- 変更したコードとテスト
- `tools/measure_footprint.py`
- `docs/perf/baseline-2026-09-23.json` と最終計測の JSON
- 改善量の比較表（`docs/perf/README.md`）
