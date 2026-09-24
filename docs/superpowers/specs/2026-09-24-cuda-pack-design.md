# GPU 高速化パックの後入れ（インストーラー サブプロジェクト 2）— 設計

- 日付: 2026-09-24
- ブランチ: `feat/cuda-pack`
- 位置づけ: インストーラーのサブプロジェクト 2（1＝基盤は `2026-09-24-installer-velopack-design.md`、develop に済み）

## 目的

アプリ本体は CPU 版の 1 種類だけを配り、NVIDIA の GPU がある人だけが、GPU で翻訳と文字起こしを速くする「GPU 高速化パック」（CUDA のライブラリ cuBLAS / cuDNN）をアプリから導入できるようにする。

**成功基準**
- NVIDIA の GPU がある PC では、初回起動で 1 回だけ導入を尋ね、「導入する」→ ダウンロード → 「再起動して GPU を使う」で、翻訳と文字起こしが GPU で動く。
- 導入・削除・失敗のどの場合もアプリは CPU で動き続け、中途半端なファイルを残さない。
- パックは `data\` の中に置くので、アプリの削除で一緒に消える。
- CUDA 版のビルド（`backend_cuda.spec`、`.venv_cuda`）が要らなくなる。

## 現状（2026-09-24 時点、develop 070075d7）

- `utils.py` は import 時に 1 回だけ、同梱の `nvidia/*/bin` と外部フォルダ `VRCT_DATA_DIR\cuda\bin`（環境変数があり、フォルダが存在するときだけ）を DLL 検索パスと `PATH` に足す。あとから足し直す処理は無い。
- デバイスの一覧（`getComputeDeviceList`）は起動時に 1 回作る。CTranslate2 の CUDA デバイス数が 1 以上でも、`cublas64_12.dll` を読めなければ GPU を一覧から隠す。
- パックが無い状態で起動すると、保存してあった GPU の選択は検証で弾かれて CPU に戻り、上書き保存される。
- 大きいファイルの取得は、full 辞書（`transliteration_dictionary.downloadSudachiFullDict`）が SHA-256 の検証・zip の展開・失敗時の片付け・進み具合の通知まで持っていて、画面の欄（`weight_download_status`）もある。途中からの再開は無い。
- サイドカーやアプリを画面から再起動する手段は、Velopack の更新の「今すぐ再起動」だけ。
- CUDA 版のビルドのために `spec/backend_cuda.spec`・`bat/build_cuda.bat`・`requirements_cuda.txt`・`.venv_cuda`（`bat/install.bat` が作る）・`package.json` の `build-python-cuda` / `dev-cuda` / `dev-cuda-fast` がある。CI も `npm run setup-python` で使わない約 1.8 GB の NVIDIA のパッケージを落としている。

### 事前の実験（2026-09-24、この PC、RTX 3070）

CPU 版の `.venv`（ctranslate2 4.6.0、faster-whisper 1.1.1）に `.venv_cuda` の cuBLAS / cuDNN の `bin` を `os.add_dll_directory` と `PATH` で読ませたところ:
- NLLB-600M（int8_float16）の翻訳が GPU で動いた（読み込み 2.0 秒、1 回 0.74 秒）。
- Whisper base（float16）の文字起こしが GPU で動いた。
- 読み込まれた DLL: `cublas64_12.dll`、`cublasLt64_12.dll`、`cudnn64_9.dll`、`cudnn_cnn64_9.dll`、`cudnn_engines_precompiled64_9.dll`、`cudnn_engines_runtime_compiled64_9.dll`、`cudnn_graph64_9.dll`、`cudnn_heuristic64_9.dll`、`cudnn_ops64_9.dll`。`cudnn_adv64_9.dll`（326 MB）と `nvblas64_12.dll` は読まれなかった。
- CTranslate2 の wheel は `.venv` と `.venv_cuda` で同じ（`ctranslate2.dll` の SHA-256 が一致）。

## 決めたこと

| 項目 | 決定 |
|---|---|
| 勧め方 | 初回起動で 1 回だけ尋ねる。あとからは設定で導入できる |
| 導入後 | 「再起動して GPU を使う」で再起動し、翻訳と文字起こしのデバイスを自動で GPU にする |
| 取得元 | NVIDIA が PyPI に公開している公式のパッケージ（wheel）から直接取る。版と SHA-256 はアプリに固定 |
| 文言 | ボタンは「導入する」（「入れる」ではなく） |

## 1. 利用者から見た流れ

- **初回起動**: NVIDIA の GPU があり、ドライバーが CUDA 12 に対応し、パックが入っていないとき、「GPU 高速化パックを導入して、翻訳と文字起こしを GPU で動かしますか？」と 1 回だけ尋ねる（ダウンロード約 1.3 GB、展開後 1.8 GB）。ボタンは「導入する」「今はしない」。どちらを選んでも、自動で尋ねるのはこの 1 回だけ。
- **設定**: 翻訳と文字起こしの「デバイス」欄の下に「GPU 高速化パック」欄を置く（中身は 1 つで両方に出す）。文字起こしのデバイス欄は音声認識エンジンが Whisper のときだけ出るので、文字起こし側の GPU 高速化パック欄も Whisper のときだけ出る（既定のエンジンは Google）。状態に応じて:
  - GPU が無い → 欄を出さない
  - ドライバーが古い → 「NVIDIA のドライバーを更新してください」
  - 未導入 → 「導入する」
  - ダウンロード中 → 進み具合（%）
  - 導入済み（再起動待ち）→ 「再起動して GPU を使う」
  - 導入済み → 「削除」
  - 削除待ち → 「次の起動で削除します」と「再起動」
- **導入中**: 初回の問いかけから導入したときは、問いかけが閉じるだけで何も見えないので、「GPU 高速化パックのダウンロードを始めました。進み具合は、設定の「翻訳」にある「GPU 高速化パック」欄で見られます。」を通知で出す。取得が終わると、どの画面にいても分かるよう「GPU 高速化パックを導入しました。再起動すると使えます。」を閉じるまで出す。
- **導入後**: 「再起動して GPU を使う」を出す。再起動後、翻訳と文字起こしのデバイスを自動で GPU（計算の種類は「自動」）にする（パックを導入した直後の起動の 1 回だけ）。音声認識エンジンは切り替えないので、文字起こしが GPU で動くのは Whisper を選んだとき。
- **削除**: 「削除」で「次の起動で削除します」と出して再起動を促す（DLL は使用中で消せないため）。次の起動で DLL を読む前に消し、デバイスは CPU に戻る。前のサイドカーが止められた直後でまだ DLL を放していないときのために少し待って試し直し、それでも消しきれなければ削除の予約を残して次の起動でもう一度消す（そのあいだパックは読み込まない）。導入して再起動する前に削除したときは、次の起動の GPU への切り替えもやめる。
- **失敗**: 通信の失敗・SHA-256 の不一致・空き容量の不足（約 3.1 GB 必要）では、何も入れずに片付けてエラーを出す。アプリは CPU で動き続け、再試行できる。前から入っていたパックの DLL をほかのプロセスが使っていて置き換えられないときは、落とす前に止めて「GPU 高速化パックが使用中のため置き換えられません。再起動してからもう一度試してください。」を出す。取得の途中でアプリが終わった（閉じた・落ちた・電源が切れた）ときに残る途中のファイルは、次の起動で消す。

## 2. 取得と展開（Python）

新しいモジュール `src-python/models/cuda_pack.py` に置く。

**固定する情報**（版を変えるときはここを書き換えて新しい版のアプリを出す）

| パッケージ | 版 | ファイル | 大きさ（バイト） | SHA-256 |
|---|---|---|---|---|
| nvidia-cublas-cu12 | 12.8.4.1 | `nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl` | 567,544,208 | `47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af` |
| nvidia-cudnn-cu12 | 9.7.1.26 | `nvidia_cudnn_cu12-9.7.1.26-py3-none-win_amd64.whl` | 715,962,100 | `7b805b9a4cf9f3da7c5f4ea4a9dff7baf62d1a612d6154a7e0d2ea51ed296241` |

URL は PyPI の JSON API（`https://pypi.org/pypi/<name>/<version>/json`）が返す `files.pythonhosted.org` の固定 URL を定数にする。パックの版の印（例: `cu12.8-cudnn9.7`）も定数にし、`pack.json` の印と違えば「未導入」として導入し直しを勧める。

**流れ**
1. 空き容量を確かめる（約 3.1 GB）。足りなければ始めない。前から入っていた `bin\` の DLL をほかのプロセス（前のサイドカーなど）が読み込んでいれば、始めずに `CUDA_PACK_IN_USE` で知らせる。読み込まれた DLL はフォルダごと名前を変えられるが消せず、書き込みでも開けないので、書き込みで開けるかで見分ける（2026-09-24 に確かめた）。
2. 2 つの wheel を `data\cuda\download\` に落とす。落としながら SHA-256 を計算し、最後に照合する。固定した大きさより多く届いたら、その時点で止める。応答は失敗しても閉じる。通信の時間切れ（接続 10 秒・読み取り 60 秒）と 3 回までの再試行は既存の取得と同じ。途中からの再開はしない。
3. 両方そろったら、wheel（zip）から `nvidia/*/bin/*.dll` だけを `data\cuda\bin.tmp\` に展開し、期待する DLL（事前の実験で読み込まれた 9 つを含む、wheel の bin にあるすべて）がそろうことを確かめる。
4. もう一度 1 の使用中の確認をしてから（使用中なら何も消さずに止める）、`data\cuda\bin\` に置き換え、版の印と DLL の一覧を書いた `data\cuda\pack.json` を置く。wheel は消す。
5. どこかで失敗したら、途中のファイル（`download\`、`bin.tmp\`）を消し、エラーコードで画面に知らせる。アプリが終わって片付けが動かなかったときは、次の起動で `utils` が DLL を登録する前に消す（起動したばかりで取得は動いていない）。

**進み具合**: 2 つの wheel の合計バイト数に対する割合（0〜1）。エンドポイントと画面の欄は既存のモデル・full 辞書の取得と同じ作り（`/run/download_cuda_pack`、`/run/download_progress_cuda_pack`、`/run/downloaded_cuda_pack`）。

**削除**: 「削除」で `data\cuda\remove_pending` を置く。次の起動で、`utils` が DLL を登録する前にこの印を見て、`pack.json`・`bin\`・`data\cuda\` の順に消す。`bin\` が消えなければ 0.5 秒おきに 5 回まで試し、それでも残れば印を残す（状態は `remove_pending` のまま、次の起動でもう一度消す）。印があるうちはパックを読み込まない。

**開発中**: `VRCT_DATA_DIR` が無い開発中の起動でも、`PATH_DATA`（= 実行ファイルのフォルダ）の下の `cuda\bin` を読む（今は環境変数が無いと外部フォルダを見ないので直す）。開発者も `.venv_cuda` なしで GPU を試せる。

## 3. GPU の判定と、導入後の切り替え

**状態**（`cuda_pack.status()`、起動時と変化のたびに画面へ送る: `/get/data/cuda_pack_status`、`/run/cuda_pack_status`）

| 状態 | 条件 |
|---|---|
| `no_gpu` | CTranslate2 の CUDA デバイス数が 0（パックが無くてもドライバーだけで数えられることを確認済み） |
| `driver_too_old` | `nvcuda.dll` の `cuDriverGetVersion` が 12000 未満 |
| `not_installed` | パックが無い、または `pack.json` の版の印が今の定数と違う |
| `downloading` | 取得中（進み具合つき） |
| `installed_restart_required` | 導入したが、この起動ではまだ読み込まれていない |
| `installed` | 導入済みで、この起動で読み込まれている |
| `remove_pending` | 次の起動で削除する |

**設定に足すもの**（保存する）
- `CUDA_PACK_PROMPTED`（真偽値）: 初回の問いかけを出したか。
- `CUDA_PACK_SELECT_GPU_ON_NEXT_START`（真偽値）: 導入が終わったら真にする。

**導入後の自動切り替え**: 起動時、`CUDA_PACK_SELECT_GPU_ON_NEXT_START` が真でデバイスの一覧に GPU があれば、翻訳と文字起こしのデバイスを最初の GPU、計算の種類を「自動」にして印を消す。GPU が出なければ（DLL が壊れているなど）切り替えずに印を消し、エラーを 1 回出す。

**削除後**: 一覧から GPU が消えるので、今の検証の仕組みで CPU に戻る。

**画面**: 初回の問いかけは状態が `not_installed` かつ `CUDA_PACK_PROMPTED` が偽のときだけ出し、どちらのボタンでも真にする。

## 4. 再起動と、CUDA 版のビルドの片付け

**再起動**（Rust）: Tauri のコマンド `app_restart` を足す。画面は「再起動して GPU を使う」／「再起動」で、今の「閉じる」と同じ手順（サイドカーに `/run/shutdown` を送って 2 秒待つ）のあとこれを呼ぶ。
- 準備済みの更新（更新役の状態が `ready`）があれば、更新役の「今すぐ再起動」と同じ道（Velopack が入れ替えてから起動し直す）を通す。入れ替え中に新しいアプリを起動すると Velopack に止められる既知の制約を避け、パックの読み込みと更新を一度に済ませるため。
- 無ければ Tauri の `AppHandle::request_restart()` で起動し直す。開発中も同じ。`RunEvent::ExitRequested` → `RunEvent::Exit` を通るので、tauri-plugin-shell がサイドカーを止め、`lib.rs` の Exit の処理も動く。`AppHandle::restart()` は使わない（下の問題）。この道では、確かめたあとで準備ができた更新は入れ替えない（起動し直すアプリと Velopack の入れ替えがぶつかるため。その版は次の起動の確認でまた見つかる）。
- サイドカーは `/run/shutdown` の処理（設定の保存を含む）が終わってから 3 秒後に自分で終わる。普通は画面が 2 秒後に閉じて Tauri が先に止めるので、親が止めなかったときの保険。止める処理が詰まっても 30 秒で終わる。

**実機で見つかった問題（2026-09-24、同じ日に修正）**: 準備済みの更新が無いときの再起動で、前のサイドカーが終わらずに残っていた。`app_restart` は `async` の付かないコマンドなのでメインスレッドで動き、Tauri 2.5.1 の `restart()` はメインスレッドから呼ばれると `RunEvent::Exit` を出さずにプロセスを起動し直す（`tauri-2.5.1/src/app.rs` 542〜566 行）。tauri-plugin-shell はサイドカーを `RunEvent::Exit` でだけ止め（`tauri-plugin-shell-2.2.1/src/lib.rs` 132〜142 行）、サイドカーは `/run/shutdown` では終わらなかった（watchdog も止まる）ので、誰も止めなかった。GPU を使っていたサイドカーが `data\cuda\bin` の cuBLAS を読んだまま残るため、削除で 2 つの DLL が消えず、そのあとの導入は落とし終えてから `PermissionError` で失敗した。直し方は上の `request_restart()` と、サイドカーが自分で終わること、削除の試し直し（§1・§2）、使用中の確認（§2）。直す前と直したあとの確認は `docs/perf/README.md` の「追記（GPU 高速化パックの後入れ）」。

**CUDA 版のビルドをやめる**
- 消す: `spec/backend_cuda.spec`、`bat/build_cuda.bat`、`requirements_cuda.txt`、`package.json` の `build-python-cuda` / `dev-cuda` / `dev-cuda-fast`、`bat/install.bat` の `.venv_cuda` を作る部分。`npm run setup-python` と CI が使わない約 1.8 GB を落とさなくなる。
- `utils.py` の、同梱の `nvidia` パッケージを探す処理は残す（手元の venv に入っていれば使える）。古い関数名のコメントを直す。
- ドキュメント（`docs/readme_build.md` など）の CUDA 版のビルド手順を「GPU 高速化パックはアプリから導入する」に書き換える。
- 手元の `.venv_cuda` フォルダは消さない。

## 5. テストと確認

**自動テスト**
- Python: 状態の判定（CUDA デバイス数とドライバーの版は差し替え可能にする）、導入の流れ（小さな偽の wheel と差し替えた定数・ダウンロードで、SHA-256 一致で展開と `pack.json`、不一致で何も残らない、容量不足で始めない、途中の失敗で片付け、進み具合が 0→1、固定の大きさを超えたら止める、応答を閉じる、使用中の `bin\` では落とす前に止める・置き換えの直前でも止める）、使用中の見分け方（別のプロセスが本当に DLL を読み込んだ状態で）、起動時の削除（消えない `bin\` では印を残す、試し直す、印があれば読み込まない）、途中で終わった取得の片付け、自動切り替え（GPU あり／なし、削除で取り消す）、`CUDA_PACK_PROMPTED` の保存、開発中でも `PATH_DATA\cuda\bin` を読むこと、`/run/shutdown` のあと自分で終わること（時計は差し替える）、消したファイルとエンドポイントが残っていないこと。
- Rust: 再起動の道の選び方（更新が `ready` なら更新役、そうでなければ `request_restart()`。`restart()` に戻っていないこともソースで確かめる）。普通の再起動では更新の入れ替えを頼まないこと。
- 画面: `npm run vite-build`、5 言語の文言、UI との約束のテスト（`test_ui_endpoint_contract.py`）。

**実機での確認**（この PC、RTX 3070。UI Automation と PrintWindow だけで操作し、マウス・キーボードは使わない）
1. Velopack で入れた版を手元の更新元で導入 → 初回の問いかけ → 「導入する」（本物の PyPI から約 1.28 GB。時間と大きさを記録）。
2. 「再起動して GPU を使う」→ デバイスが GPU、実際に GPU で翻訳・文字起こしができる（サイドカーが cuBLAS / cuDNN を読み込んでいることをプロセスで確認）、CPU との速さを記録。
3. 削除 → 再起動 → CPU に戻り、`data\cuda` が消える。
4. もう一度導入し、準備済みの更新がある状態で再起動 → 更新とパックの読み込みが一度に済む。
5. アプリを削除 → パックも含めて何も残らない。

結果（2026-09-24）は `docs/perf/README.md` の「追記（GPU 高速化パックの後入れ）」。1・2・4・5 は期待どおり（取得 1.28 GB は 67〜126 秒、展開後 1,766 MiB、翻訳は GPU で約 4.3 倍）。3 は最初の確認では §4 の問題で DLL が 2 つ残ったが、直したあとの確認（同じ追記の「修正後の再確認」）で、再起動のあとのサイドカーが 1 つで `data\cuda` が消えることを確かめた。文字起こしは Whisper のモデルを GPU に作るところまでで、声を入れた推論は確かめていない。

## 範囲外

- 使われない DLL（`cudnn_adv64_9.dll` など）を外して容量を減らすこと
- 途中からの再開（Range）
- AMD / Intel の GPU
- OCR（onnxruntime）の GPU 化
- 元の VRCT からの取り込み（サブプロジェクト 3）
