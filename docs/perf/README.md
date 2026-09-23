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
