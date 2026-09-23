# 軽量化の計測記録

A-1（Python サイドカーのスリム化）の計測結果を置く。
計測方法は `tools/measure_footprint.py` の docstring を参照。

## 作業開始時のテスト結果（2026-09-23）

- pytest: 936 passed, 43 warnings in 30.34s
- 既知の失敗: なし

## 基準値（作業前）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| CPU | 1186.9MB | -（Rust 未導入のため未計測） | 6.72秒 | 264.6MB | - |
| CUDA | 2950.8MB | -（Rust 未導入のため未計測） | 7.80秒 | 409.3MB | - |

import 時間の上位 5（CPU 版）: mainloop: 4978.2ms, controller: 4956.6ms, model: 3976.1ms, models.translation.translation_translator: 3271.8ms, models.translation.translation_providers: 2344.1ms

## langchain 撤去後（Task 5, 2026-09-23）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| langchain 撤去後（CPU） | 1180.5MB | -（Rust 未導入のため未計測） | 4.70秒 | 220.5MB | - |

import 時間の上位 5（CPU 版）: mainloop: 2684.6ms, controller: 2667.1ms, model: 1808.5ms, models.translation.translation_translator: 1378.1ms, models.translation.translation_providers: 864.2ms

## transformers 撤去後（Task 7, 2026-09-23。fix round 1 で計測をやり直し）

CTranslate2 のトークナイザを sentencepiece 直呼びの `CT2Tokenizer`（Task 6）に差し替え、
`transformers` をアプリ本体の依存から撤去した。`transformers` は
`requirements-dev.txt` にトークナイザ照合テスト専用として残るため、通常の開発環境の
`.venv` には常にインストールされている。

`ctranslate2/__init__.py` が無条件に `ctranslate2.converters` を import し、
その `__init__.py` が `try/except ImportError` 越しに `import transformers` する
(pyinstaller-hooks-contrib の `hook-transformers.py` も効く)。PyInstaller の
modulegraph はこの try/except 内の import も辿るため、`.venv`/`.venv_cuda` に
`transformers` さえ入っていれば `VRCT_PYINSTALLER_CLEAN=1` の有無に関わらず
毎回ビルドに巻き込まれる（fix round 1 以前の記述にあった「キャッシュが古いせい」
は誤りで、原因は spec 側で除外していなかったこと）。
`spec/backend.spec` / `spec/backend_cuda.spec` の `excludes` に `transformers` と
`torch` を追加して対処した（ctranslate2 側は ImportError を握りつぶすだけなので
実行時に影響しない）。

以下は、通常の開発者 `.venv`（`requirements-dev.txt` 込みで `transformers` が
入った状態）から `bat\build.bat`（`VRCT_PYINSTALLER_CLEAN=1` 付きで 1 回）を
実行し、`src-tauri\bin\_internal` に `transformers`/`torch` ディレクトリが
無いことを確認した上で計測した値。（fix round 1 で単位を MiB (bytes/1024²)
に統一。以前の記載は誤って bytes/1e6 の十進 MB で計算していた。）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| transformers 撤去後（CPU） | 1116.8MB | -（Rust 未導入のため未計測） | 5.77秒 | 212.9MB | - |

import 時間の上位 5（CPU 版）: mainloop: 3289.4ms, controller: 3269.8ms, model: 2341.4ms, models.translation.translation_translator: 1664.2ms, models.translation.translation_providers: 1020.0ms

## 遅延 import 後（Task 11, §5, 2026-09-24）

`controller.init()` の中で必ずは使わない重いモジュールの import を、実際に
使う関数の中まで遅らせた（インストールされていない環境でも起動を落とさない
よう、いずれも `try/except` + モジュール属性キャッシュの形を保つ）。

遅延させたモジュールと、`import mainloop` 単体での累積 import 時間
（Step 1 計測、開発 `.venv` 上、非フリーズ実行）:

- `translators`（Google/Bing/Papago 経由のWeb翻訳）: 542.5ms
  （`ENABLE_TRANSLATORS` は `find_spec` による有無判定のみに変更、実 import は
  `models/translation/translation_translator.py` の `_other_web_translator()`
  が最初の呼び出し時に行う）
- `google.genai`（Gemini 翻訳）: 533.2ms
  （`models/translation/translation_gemini.py` の `_genai()`）
- `openai`（OpenAI/Groq/OpenRouter/Plamo/OpenAI互換 の翻訳・文字起こし）: 269.6ms
  （`translation_openai.py`/`translation_groq.py`/`translation_openrouter.py`/
  `translation_plamo.py`/`translation_openai_compatible.py`/
  `transcription_openai_compatible.py`/`transcription_providers.py` それぞれに
  `_openai()` を追加。`translation_llm_common.py` は Task 3〜5 の時点で
  既に関数内 import 済み）
- `faster_whisper`（Whisper文字起こし）: 58.2ms
  （`models/transcription/transcription_whisper.py` の `_whisperModelClass()`。
  ブリーフ通り `WhisperModel` 属性名は維持）
- `rapidocr`（OCR文字認識）: 累積で約200ms
  （`models/ocr/ocr_engine_rapidocr.py` の `_rapidocr()`）
- `OpenGL`/`glfw`（OpenVR ミラーテクスチャ経由のOCRキャプチャ）: 合計約95ms
  （`models/ocr/ocr_capture_openvr.py` の `_gl()`/`_glfw()`。同ファイルの
  `openvr` も同じ形で遅延させたが、`openvr` 単体は約11msで閾値未満のため
  `test_lazy_imports.py` の必須リストには含めていない）

対象から外したもの（CodeGraph で `controller.init()` からの到達性を確認した上で判断）:

- `ctranslate2`: `config.py` の `Config.init_config()`（`config` シングルトン
  構築時、つまり `import mainloop` の時点）が `utils.getComputeDeviceList()`
  経由で `get_supported_compute_types`/`get_cuda_device_count` を呼び、
  `SELECTABLE_COMPUTE_DEVICE_LIST`/`_COMPUTE_MODE` を決めている。さらに
  `controller.init()` 自体も起動直後に
  `model.checkTranslatorCTranslate2ModelWeight()` を呼ぶ。どちらも起動時に
  必ず使うため対象外にした。ただし `utils.py`（Step 4）・
  `translation_translator.py`/`translation_utils.py`（Step 5）の
  `_ctranslate2()`/`_ct2_get_*` 化はブリーフ通り実施済みで、関数名は
  既存テストが patch するものと同じに保っている。
- `cv2`/`onnxruntime`（`models/ocr/ocr_bubble_detector.py`）: 既存テスト
  (`test_ocr_bubble_detector.py`) がモジュール属性 `cv2`/`ort` を直接 `None`
  に patch して「未インストール環境」を模擬しており、`None` を「未ロード」の
  センチネルに使う遅延ローダー方式とこのテスト契約が衝突する
  （`patch(..., None)` すると次の呼び出しで本物の再 import が走ってしまう）。
  解消には別センチネル方式への設計変更が要り、単純な import 移動を超えるため
  今回は対象外とした。
- `openvr`（`models/overlay/overlay.py`/`models/clipboard/clipboard.py`/
  `models/openvr_session.py`）: `openvr_session.acquire()` のデフォルト引数が
  `openvr.VRApplication_Background` を直接参照しており、遅延させるには
  関数シグネチャの変更が要る。`openvr` 単体の累積時間も約11msで50ms未満の
  ため対象外とした（`models/ocr/ocr_capture_openvr.py` 側は既存の
  `openvr`/`GL`/`glfw` いずれも別ファイルなので遅延済み）。
- `yaml`/`requests`/`psutil`/`pythonosc`/`websockets`/`deepl`/`sudachipy`:
  ブリーフの「必ず使う」指定、または累積50ms未満のため対象外。

`bat\build.bat`（`VRCT_PYINSTALLER_CLEAN=1`）でビルドし、
`src-tauri\bin\_internal` を確認した結果、`ctranslate2`/`cv2`/`faster_whisper`/
`onnxruntime`/`rapidocr` はディレクトリとして展開されており、`openai`/
`translators`/`google.genai`/`OpenGL`/`glfw` は（純Pythonパッケージのため）
`PYZ-00.pyz` 内に収録されていることを `python -m
PyInstaller.utils.cliutils.archive_viewer -r -b` で確認した。`hiddenimports`
の追加は不要だった。

遅延 import 後の計測（`tools\measure_footprint.py`、Rust 未導入のためインストーラー未計測）:

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| 遅延 import 後（CPU） | 773.8MB | -（Rust 未導入のため未計測） | 2.99秒 | 150.6MB | - |

import 時間の上位 5（遅延 import 後、CPU 版。開発 .venv のインタープリタで
`-X importtime` を用いて計測しており、フリーズ済みバイナリの計測ではない）:
mainloop: 860.2ms, controller: 845.2ms, config: 315.5ms, device_manager: 310.7ms, model: 204.8ms

（bin 合計・アイドル RSS・起動時間は前段の Task 8〜10 (Sudachi 辞書 core 切替) 後の
状態と比べての差分であり、この行だけでは §5 単独の寄与分を切り分けられない。
mainloop の import 時間は前回記録の transformers 撤去後 3289.4ms から
860.2ms へ大きく縮んでおり、遅延させたモジュール群 (§5) の効果が支配的と見て良い。）

## 結果（A-1 完了時）

Task 13 の最終計測。`bat\build.bat`／`bat\build_cuda.bat`（いずれも
`VRCT_PYINSTALLER_CLEAN=1`）でそれぞれフリーズし直し、
`src-tauri\bin\_internal` に `transformers`／`torch`／`sudachidict_full`／
`langchain*`／`grpc` のディレクトリが存在しないことを `Get-ChildItem` で
確認した上で `tools\measure_footprint.py`（`--python` に `.venv` /
`.venv_cuda` の絶対パスを指定）で計測した
（`docs/perf/final-cpu-2026-09-24.json` / `docs/perf/final-cuda-2026-09-24.json`）。
Rust 未導入のためインストーラーは未計測。モデル読込後 RSS は
`measure_footprint.py` 自体が `_MODEL_RSS_SCRIPT` でモデルをロードして計測する
実装になっているが、`src-tauri\bin\weights\whisper\small` が存在しない環境
だったため、ロード対象が無く結果が null になった（未実装ではなく、対象の
モデル重みが未配置だったことによる欠測）。

単位は MB = 1024² bytes（MiB）に統一している。すべてのセルは
`docs/perf/*.json` の生値から `bytes/1024²` で MB を、秒はそのまま
小数第2位まで計算し、差分は丸める前の生値どうしの差から算出した。

| 版 | 指標 | 基準値 | 最終 | 差 |
|---|---|---|---|---|
| CPU | bin 合計 | 1186.9MB | 773.8MB | -413.1MB (-34.8%) |
| CPU | インストーラー | -（Rust 未導入のため未計測） | -（Rust 未導入のため未計測） | - |
| CPU | 起動（中央値） | 6.72秒 | 3.36秒 | -3.36秒 (-50.0%) |
| CPU | アイドル RSS | 264.6MB | 150.5MB | -114.1MB (-43.1%) |
| CPU | モデル読込後 RSS | -（未計測） | -（未計測） | - |
| CUDA | bin 合計 | 2950.8MB | 2536.8MB | -414.0MB (-14.0%) |
| CUDA | インストーラー | -（Rust 未導入のため未計測） | -（Rust 未導入のため未計測） | - |
| CUDA | 起動（中央値） | 7.80秒 | 3.53秒 | -4.26秒 (-54.7%) |
| CUDA | アイドル RSS | 409.3MB | 295.1MB | -114.2MB (-27.9%) |
| CUDA | モデル読込後 RSS | -（未計測） | -（未計測） | - |

### 効いた変更

途中計測（`after-langchain`/`after-transformers`/`after-lazy-import` の各
JSON、いずれも CPU 版）から読み取れる寄与は以下の通り。各行は直前の行からの
差分であり、後段ほど前段の変更を含んだ状態からの追加差分になる。

単位は MB = 1024² bytes（MiB）に統一している（fix round 1 で
`after-transformers` 行の混在を修正）。

- **langchain 撤去**（Task 5）: bin 1186.9→1180.5MB（-6.4MB）、起動
  6.72→4.70秒（-2.02秒）、アイドル RSS 264.6→220.5MB（-44.1MB）。起動時間と
  RSS への寄与が大きい。
- **transformers 撤去**（Task 7）: bin 1180.5→1116.8MB（-63.6MB）、
  アイドル RSS も 220.5→212.9MB（-7.7MB）と改善している。起動時間は
  4.70→5.77秒（+1.07秒）とこの計測では悪化しているが、後続の遅延 import 後
  計測で大きく改善しており、実行環境側のノイズ（他プロセス負荷・
  ディスクキャッシュ状態）とみている。
- **Sudachi 辞書 full→core 切替**（Task 8〜10）と**遅延 import**（Task 11,
  §5）: この 2 つは同じ「遅延 import 後」の 1 行（transformers 撤去後から
  bin 1116.8→773.8MB、起動 5.77→2.99秒、アイドル RSS 212.9→150.6MB）にしか
  記録されておらず、個別の寄与は分離できない（README 上でも同様に注記済み）。
  ただし `sudachidict_full`（約 343MB）が bin から外れたことと、mainloop の
  import 時間が transformers 撤去後の 3289.4ms から 860.2ms へ大きく縮んだ
  ことから、bin サイズの縮小は主に Sudachi full 撤去、起動時間の短縮は主に
  遅延 import の効果と推測される。
- **単一ビルド化**（Task 12, §7）を経た今回の最終計測は、遅延 import 後の
  行（bin 773.8MB / 起動 2.99秒 / アイドル RSS 150.6MB）とほぼ同水準
  （bin 773.8MB / 起動 3.36秒 / アイドル RSS 150.5MB）であり、単一ビルド化
  自体による bin サイズ・RSS への追加の悪化は見られない。起動時間の差
  （2.99→3.36秒）は実行環境のノイズの範囲内とみている。

### 悪化した指標

なし（すべての指標が基準値から ±5% を超えて改善している）。

### 結果（Step 4: 実機での通し確認、置き換え版）

Rust が未導入のためインストーラー経由の実機確認は行っていない。代わりに
`.venv`（開発環境、CPU）でのヘッドレス確認を行った。

- **CTranslate2 翻訳**: `src-tauri\bin\weights` にある CT2 モデルで
  `Translator` を構築し、翻訳を実行して出力を確認した。
- **Transliterator（読みがな）core**: `Transliterator`（core 辞書）で
  カタカナ変換の出力を確認した。
- **OCR エンジン構築**: `models/ocr` の RapidOCR パイプラインを構築し、
  例外なく初期化できることを確認した。

（`Translator` 構築・翻訳実行、`Transliterator` によるカタカナ変換出力、
RapidOCR パイプライン構築のいずれも例外なく完了したことを標準出力で確認した。）

以下は今回のセッションでは実施していない（要ユーザー確認）:

- インストーラーのビルド／インストール（Rust 未導入のため）
- マイクでの文字起こし（アプリ実機）
- API 翻訳（キーを用いた実通信）
- full 辞書の UI からのダウンロード
- full 辞書そのものでの読みがな変換（`Transliterator` に `system_full.dic`
  を渡すケース。今回ヘッドレスで確認したのは core 辞書のみ）
- 設定 UI での表示確認
- OCR のキャプチャ（実機・実画面）
- アプリ上での GPU 推論（CUDA 版実機）

## 追記（2026-09-24）: 配布パッケージのサイズと画面での確認

Rust を導入して `npm run build` を実行した（CPU 版、develop `b87f7eb9` 時点）。

- インストーラー（`VRCT_3.5.1-beta.1_x64-setup.exe`）は 2.9MB。
  アプリ本体は含まず、インストール時に CPU 版 / GPU 版のパッケージ（`VRCT.zip` / `VRCT_cuda.zip`）を
  ダウンロードして展開するダウンロード型。サイズの比較対象はパッケージのほうになる。
- パッケージ（`utils\zip.py` で作る `VRCT.zip`、CPU 版）:

| 項目 | フォーク元の記録（2026-08、`src-tauri/nsis/template.nsi` のコメント） | 今回 |
|---|---|---|
| VRCT.zip | 約 485MB | 361.7MB |
| 展開後 | 約 1.5GB | 811.8MB |

  フォーク元の値はテンプレートのコメントに残っている概算で、同じ条件で測った値ではない。
- 注意: インストーラーのダウンロード元は `feat/distribution-github-releases` ブランチ以降、
  このフォークの GitHub Releases (`lighfu/VRCT-0`) に切り替わっている(旧: Hugging Face の
  `ms-software/VRCT`/`ms-software/VRCT-beta`)。本ドキュメントの数値測定時点ではまだ
  切り替え前で、インストーラーを実行すると VRCT-0 ではなくフォーク元のパッケージが
  入る状態だったため、インストールでの確認はしていない。

未実施の一覧のうち、次は 2026-09-24 に `npm run dev-ui` で画面を起動して確認した:

- 設定 UI での表示（Other の「Reading Dictionary」、既定は Standard、未取得の full は選べない）
- full 辞書の UI からのダウンロード（取得後に full を選べるようになる。展開後 359,725,440 バイト）
- full 辞書そのものでの読みがな変換（動くが、複合語で読みの按分が崩れる既存バグあり: https://github.com/lighfu/VRCT-0/issues/1）
- 画面からの送信と CTranslate2 翻訳

## 追記（AI CLI 翻訳）: 実際の CLI での確認（2026-09-24）

`feat/ai-cli-translation`（`e9fdb051`）で、この PC に入っている 3 つの CLI
（codex-cli 0.155.1 / Claude Code 2.1.281 / agy 1.2.9）を実際に使って確かめた。
計測ではなく動作確認の記録で、値はどれも 1 回ずつ測ったもの。

サイドカー単体（`bat\build.bat` で作り、UI と同じ stdin/stdout で操作）:

| CLI | モデル（既定） | 接続確認 | 1 回目 | 2 回目 |
|---|---|---|---|---|
| codex | gpt-6-astra | 1.6 秒 | 3.9 秒 | 2.4 秒 |
| claude | haiku | 0.6 秒 | 4.2 秒 | 2.7 秒 |
| agy | gemini-3.8-flash-high | 6.6 秒 | 7.5 秒 | 3.6 秒 |

- 接続確認は `/set/data/selected_ai_cli_tool` を送ってから `/run/ai_cli_connection` が true で届くまで（モデル一覧の取得を含む）。
  接続確認のあと裏でセッションの起動が始まるので、1 回目には起動の残りが入る。
- 1 回目・2 回目は `/run/send_message_box` を送ってから訳文つきの応答が返るまで。
  原文は「こんにちは、元気ですか？」と「今日はいい天気ですね。」で、3 つとも
  「Hello, how are you?」「The weather is nice today, isn't it?」が返った。
- 2 回目の目標（claude は数秒、agy・codex は 10 秒以内）は 3 つとも満たした。
  claude の起動は事前検証の 31 秒よりずっと短く、1 回目も 4 秒台だった。

画面（`npm run dev-ui`）:

- 翻訳の設定に「AI CLI」「AI CLI の接続確認」「AI CLI のモデルを選択」が出る（ja・en とも確認）。接続確認ボタンも動く（agy で 4.7 秒）。
- CLI を切り替えるとモデル一覧が変わる（claude は 3 件、codex は 8 件、agy は 14 件）。
- メイン画面の翻訳エンジンで「AI CLI」を選べ、3 つの CLI それぞれでチャットの訳文が表示された。
  所要時間（開発モードのログから）は codex 3.9 秒・2.4 秒、claude 1.9 秒・1.4 秒、agy 3.6 秒（1 通だけ）。

プロセスの後片付け:

- サイドカーを `/run/shutdown` で終えたあとも、画面を閉じたあとも、起動した CLI
  （claude / agy / codex と、その子の node など）は残らなかった。
- `/run/shutdown` を送らずにサイドカーを強制終了しても残らなかった（CLI は標準入力が閉じると自分で終わる）。
- CLI を切り替えると前の CLI のプロセスは終わり、常駐するのは 1 本だけ。

確認できなかった点・気になった点:

- agy の許可要求: 実物の agy はヘッドレスの stream-json では許可を求めるイベントを出さない。
  許可が要る道具（`run_command`）は agy 自身が断り、`step_update`（`state: "ERROR"`）のあと
  `status: "SUCCESS"`・`response: ""`・`denied_actions` つきの `result` で 4 秒ほどでターンが終わる。
  いまの `AgySession` はイベント名に `permission` が含まれるかで見ているので反応せず、この場合は空の訳文を返す。
  また、agy 自身の作業フォルダ（`~/.gemini/antigravity-cli/scratch`）へのファイル書き込みは許可なしで通った。
  翻訳する文に道具を使わせる指示が混ざったときの扱いは、別に検討が要る。
- codex の app-server はユーザーの codex の設定を読み、設定済みの MCP サーバー（この PC では codegraph）や
  cua_node のランタイムを子プロセスとして起動する。終了時にはまとめて終わる。
- 既定の CLI は、設計書の「検出された最初の CLI」（この PC では codex）ではなく claude になる。
  既定のモデルは一覧の先頭（codex は gpt-6-astra、agy は gemini-3.8-flash-high）。
- 未ログインや、CLI が応答しないときの立ち直りは、実際の CLI では試していない（偽の CLI を使うテストだけ）。

## 追記（AI CLI 翻訳の修正後）: ツールの封じ込めと再計測（2026-09-24）

最終レビューの指摘（CLI のツールが生きたまま他人の発言を渡していた、など）を直したあと、
同じ PC・同じ 3 つの CLI（codex-cli 0.155.1 / Claude Code 2.1.281 / agy 1.2.9）で確かめ直した。
計測ではなく動作確認の記録で、値はどれも 1 回ずつ測ったもの。

サイドカー単体（`bat\build.bat` で作り直し、上の表と同じ手順）:

| CLI | 接続確認 | 1 回目（直後に送信） | 2 回目 | 1 回目（15 秒待ってから） | 2 回目 |
|---|---|---|---|---|---|
| codex | 4.4 秒（うち待ち 2.7 秒）| 3.9 秒（前回 3.9） | 2.3 秒（前回 2.4） | 1.8 秒 | 3.3 秒 |
| claude | 1.6 秒 | 7.4 秒（前回 4.2） | 2.6 秒（前回 2.7） | 1.4 秒 | 1.7 秒 |
| agy | 4.0 秒（前回 6.6） | 8.4 秒（前回 7.5） | 2.4 秒（前回 3.6） | 2.2 秒 | 1.8 秒 |

- 接続確認のあと、裏で「OK」を訳させる確認のターンを 1 回送るようにした（未ログインなどを最初のメッセージより前に見つけるため）。
  接続確認の直後に 1 回目を送ると、その確認のターンが終わるのを待つので claude と agy の 1 回目は前より長い。
  15 秒待ってから送ると（確認のターンが終わっていると）、1 回目から 2 秒前後になる。
- agy の接続確認が速くなったのは、モデル一覧を 1 回しか取らなくなったため（前回は 2 回）。
- codex の接続確認の「待ち 2.7 秒」は、起動直後に裏で取っていた agy のモデル一覧（どのタブも AI CLI を
  使っていないので、起動の後に回した分）が終わるのを待った時間。確認そのものは 1.7 秒。
- 起動中の翻訳エンジンの確認は 0.4 秒で終わった（どのタブも AI CLI を使っていないときは、モデル一覧を起動中に取らない）。

ツールを使わせる指示（害のないもの）を混ぜた確認:

- チャットから次の 4 通を送った（アプリと同じ経路）: スクラッチのフォルダに置いた目印のファイルを読んで中身を出す /
  https://example.com を取得して題名を出す / `echo hi` を実行する / 使える MCP ツールを列挙する。
  3 つの CLI とも、どれも 2〜6 秒で「その文の訳」を返しただけで、ツールは使わず、目印の文字列も出なかった。
  そのあとの普通の翻訳も正しかった。
- 翻訳の指示を付けずに、同じ 4 つを CLI に直接送っても（より強い条件）、ツールを使ったイベントは 1 つも出なかった
  （claude と agy は「翻訳しかできない」と答え、codex は文をそのまま返した）。
- 修正前の agy は、同じ「ファイルを読め」でスクラッチのファイルを読み、中身を訳文として返していた
  （agy はシステムの一時フォルダの読み取りを既定で許している）。
- agy のツールの無いエージェントに残る唯一のツール（`manage_task`）をわざと使わせると、見張りがそのターンを
  6.4 秒で失敗にしてプロセスを作り直し、次のターンは普通に返った。
- codex のプロセスの木は `cmd → node → codex.exe` だけになった（前回あった codegraph の MCP サーバー、
  cua_node / node_repl、通知プログラムは起動しない）。
- ユーザーの CLI の保存場所（`~/.gemini/antigravity-cli` の会話・`~/.codex/sessions`・codex のスレッドの履歴 DB）に、
  翻訳した文は増えなかった。agy の会話は VRCT 専用のホーム（`src-tauri\bin\ai_cli_agy_home`）に作られ、
  プロセスを止めたときに消えた。codex のデバッグ用ログ（`~/.codex/logs_2.sqlite`）には、送ったターンの本文が残る
  （設計書の「安全性」と UI の説明文に記載）。

未ログインの claude（中身が空の `CLAUDE_CONFIG_DIR` で再現。実際のログアウトはしていない。AICliClient を直接使った）:

- 接続確認の相当（CLI の選択とモデルの選択）のあと、2.1 秒で確認のターンが「Not logged in · Please run /login」で
  失敗し、「使えなくなった」という状態の変化が 1 回だけ通知された（Controller はこれを `/run/ai_cli_connection` で
  UI に送る。これは単体テストで確認）。以後の翻訳は CLI を呼ばずに即座に（1 ms 未満で）失敗する（アプリでは CTranslate2 で訳される）。

プロセスの後片付け: 2 回のサイドカーの実行とも、終了後に起動した CLI・VRCT のプロセスは残らなかった。
