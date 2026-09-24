# インストーラー（基盤）— Velopack への移行 — 設計

- 日付: 2026-09-24
- ブランチ: `feat/installer-velopack`
- 位置づけ: ロードマップ「よりスマートなインストーラーで簡単インストール、アンインストール、更新」のサブプロジェクト 1

## 目的

導入・更新・削除を簡単で安全にする。ユーザーが挙げた優先順は「更新を安全・軽く」→「きれいに消せる」→「導入の手数を減らす」→「GPU 版を後から足す」。

**成功基準**
- 導入: `VRCT-0-win-Setup.exe` を開くだけで、ページを出さずに入って起動する。
- 更新: アプリの中で通知 → 押すと差分を落とす → 再起動（今すぐ／終了時）で入れ替わる。途中で失敗しても旧版で起動できる。更新しても設定とモデルは残る。
- 削除: Windows の「設定 → アプリ」から消すと、VRCT-0 が作ったものが何も残らない。
- 元の VRCT が入っている PC でも、元の VRCT に触れずに並べて入れられる。

## 現状（2026-09-24 時点）

- `VRCT_setup.exe`（2.9 MB）は NSIS のダウンロード型インストーラー。ユーザー単位（管理者権限なし）で `%LOCALAPPDATA%\VRCT` に入れ、GitHub Releases から `VRCT.zip`（CPU、362 MB）か `VRCT_cuda.zip`（GPU、1.53 GB）を落として展開する。テンプレートは `src-tauri/nsis/template.nsi`（約 1,500 行）。
- アプリ内の更新は Python サイドカーが `VRCT_setup.exe` を落として起動し、アプリを閉じる。
- 問題点:
  1. 別の版への更新では、旧版をアンインストールしてから新版を落とす。落とすのに失敗するとアプリが消えたまま戻せない。
  2. 更新のたびに 362 MB〜1.5 GB を丸ごと落とす。CPU↔GPU の切り替えも全部入れ直し。
  3. CUDA DLL を後から足す仕組みは読む側（`utils.py`、`%LOCALAPPDATA%\VRCT\cuda\bin`）だけで、取ってくる側が無い。
  4. ウィザードのページが多い（ようこそ・ライセンス・言語・CPU/GPU・空のコンポーネント・再インストール・場所・スタートメニュー・完了）。
  5. 削除で残るもの: `ai_cli_workspace`、`VRCT_setup.exe`、`cuda\`、レジストリの値、`%TEMP%\*.zip.bad`。データ削除のチェックは既定でオフ。
  6. 実行中のサイドカーを検出しない。コード署名が無い。
- 設定・ログ・モデル・辞書はすべてアプリ本体と同じフォルダ（`PATH_LOCAL` = サイドカーの実行ファイルのフォルダ）にある。
- アプリの名前・ID・導入先・レジストリキーは元の VRCT と同じ（`VRCT` / `com.vrct.app` / `%LOCALAPPDATA%\VRCT` / `HKCU\Software\m's software\VRCT`）。
- `lighfu/VRCT-0` にはまだ公開リリースが無い。

## 決めたこと

| 項目 | 決定 |
|---|---|
| 方式 | Velopack に移行する（NSIS 改修・自前ランチャーと比較して選択） |
| 元の VRCT との関係 | 別アプリとして並べて入れる |
| 更新の流れ | 通知して、ユーザーが押したら更新（勝手に再起動しない） |
| 削除時のデータ | 全部消す |
| 署名 | 範囲外（証明書を用意できたら CI に足す） |

## サブプロジェクトの分け方

1. **基盤**（この spec）: Velopack の導入、データの置き場所、ワンクリック導入、アプリ内更新、削除、CI でのリリース。
2. **GPU 部品の後入れ**: アプリ本体を CPU 版の 1 種類にまとめ、CUDA の DLL（約 1.2 GB）は必要な人だけがアプリから取る。
3. **元の VRCT からの取り込み**: 初回起動時に、元の VRCT の設定とモデルを取り込むか尋ねる。

2 が終わるまで公開リリースは出さない（出すと GPU 版の利用者が困るため）。

## 1. 名前と置き場所

- 表示名 `VRCT-0`、Tauri の identifier `com.lighfu.vrct0`、本体の実行ファイル `VRCT-0.exe`、Velopack の packId `VRCT-0`。
- 導入先とフォルダ構成（Velopack の標準構成 + `data\`）:

```
%LocalAppData%\VRCT-0\
  current\     アプリ本体（更新のたびに丸ごと入れ替わる）
  data\        設定・ログ・モデル・辞書・CUDA 部品・AI CLI の作業フォルダ・WebView2 のデータ・起動ログ
  packages\    Velopack が落とした更新
  Update.exe
  VRCT-0.exe   起動用の小さな実行ファイル（Velopack が置く）
```

- `data\` は `current\` の 1 つ上にあるので更新では消えず、削除ではフォルダごと消える（Velopack の仕様）。
- VRCT-0 はこのフォルダの外に何も書かない。WebView2 のデータ（今は `%LOCALAPPDATA%\com.vrct.app`）も `data\webview` に移す。
- 開発中にリポジトリから起動するとき（Velopack で入れていないとき）は、今までどおり実行ファイルのフォルダをデータ置き場にする。

## 2. 導入と削除

**導入**（`VRCT-0-win-Setup.exe`）
- ページを出さずに `%LocalAppData%\VRCT-0` に入れ、スタートメニューとデスクトップにショートカットを作って起動する。入れている間は VRCT-0 の画像と進み具合を出す（`vpk pack --splashImage`）。
- 今インストーラーで選ばせているものはなくす:
  - 言語: 初回起動時（config.json が無いとき）に OS の表示言語（`GetUserDefaultUILanguage`）から決める。今はこの処理が無い（既定は英語）ので新しく作る。`installer_language.txt` の受け渡しは廃止。
  - CPU / GPU: サブプロジェクト 1 では CPU 版のみ。
  - 導入先・ライセンス・スタートメニューのページ: 出さない。
- Velopack の標準の引数（`-s` で黙って入れる、`--installto` で導入先を変える）は使える。
- WebView2: Windows 11 には入っている。Windows 10 で無い場合に備えて `vpk pack --framework webview2` で前提として入れる（2026-09-24 に vpk 1.2.0 で指定できることを確認）。

**削除**（Windows の「設定 → アプリ」から）
- Velopack が `%LocalAppData%\VRCT-0` を丸ごと消す（ショートカットと Windows のアンインストール登録も消える）。`data\` も中にあるので、設定・モデル・辞書・ログ・CUDA 部品・AI CLI の作業フォルダまで消える。
- 削除の直前のフック（Velopack の決まりで 30 秒以内に終える）で、このフォルダから動いているプロセス（本体・サイドカー）を止める。AI CLI の子プロセスはサイドカーが終わると標準入力の終わりを受けて自分で終了する（2026-09-24 に実機で確認済み）。
- AI CLI 自身が自分の保存場所に書くもの（例: codex のデバッグログ `~/.codex/logs_2.sqlite`）は VRCT-0 のものではないので消さない。

**捨てるもの**
- `src-tauri/nsis/`（テンプレート、プラグイン 4 つ、ライセンス）と `tauri.conf.json` の NSIS 設定
- Rust の未使用コマンド `download_zip_asset`
- NSIS 由来の処理（`installer_language.txt` の読み込み、起動時のチャンネル修復）
- NSIS で入れた VRCT-0 からの移行処理は作らない（公開リリースが無いため）。

## 3. アプリ内の更新

**仕組み**: 更新は Rust 側（Tauri 本体）で Velopack の Rust クレート `velopack`（2026-09-24 時点 1.2.158）の `UpdateManager` を使う。参照先は GitHub Releases `lighfu/VRCT-0`（GitHub のソースを使う。ベータ版チャンネルならプレリリースも対象）。
画面からは Tauri のコマンドで呼ぶ: 確認・ダウンロード（進み具合はイベントで送る）・適用（今すぐ／終了時）。
Python 側の更新処理（`checkSoftwareUpdated`、`listAvailableReleases`、`_downloadVerifiedSetup` など setup.exe を落として起動する処理、`/run/update_software` と `/run/update_cuda_software`、`/run/software_update_info`）は削除する。

**流れ**
1. 起動して画面が出たあと、裏で 1 回だけ新しい版を確かめる。見つかったら「新しい版 vX.Y.Z が出ています（約 N MB）」とリリースノートへのリンクを通知する（サイズは差分があれば差分）。
2. 「更新」を押すと裏でダウンロードする。進み具合は更新の設定欄に出し、その間もアプリは使える。差分が使えないときは Velopack が丸ごとのパッケージに切り替える。
3. 落とし終えたら「今すぐ再起動して更新」と「終了するときに更新」の説明を出す。何も押さなければ、次にアプリを閉じたときに黙って入れ替える（再起動はしない）。アプリが落ちて閉じる処理を通らなかった場合は、次の起動時に Velopack が自動で入れ替える（Velopack の既定の動き。計画の段階で `VelopackApp::run()` の実装を読んで確認）。
4. 入れ替えに失敗しても、旧版の `current\` はそのまま残って起動する（実装計画で Velopack の適用処理の振る舞いを確かめ、実機でも確かめる）。次の確認でまた通知する。

**設定欄**（今の「更新」セクションを作り直す）
- チャンネル（安定版／ベータ版）: 今の `SELECTED_RELEASE_CHANNEL` を使う。画面が Rust のコマンドに渡す。初回（config.json に値が無いとき）の既定値は、この版がプレリリースならベータ版、そうでなければ安定版（ベータ版の Setup で入れた人に、いきなり安定版へ戻す更新を勧めないため）。起動のたびに版からチャンネルを上書きする NSIS 由来の処理はやめる。
- 今の版の表示と「更新を確認」ボタン。
- 版を選ぶ一覧（古い版に戻す機能）はなくす。例外: ベータ版から安定版に切り替えたときは、版が下がっても最新の安定版を入れる。
- CPU / GPU の選択はここから外す（サブプロジェクト 2 で「デバイス」の設定に移す）。

**うまくいかないとき**
- 通信できない・GitHub の回数制限: 起動時の確認なら通知を出さずログだけ。「更新を確認」を押したときは失敗したと表示する。
- ダウンロードの失敗: 「更新に失敗しました」と再試行ボタン。何も入れ替えない。
- Velopack で入れていない（開発中の起動など）: 更新欄に「開発版のため更新できません」と表示する。

## 4. データの置き場所の切り替えとリリースの作り方

**データの置き場所**
- Rust 本体は起動の最初に `VelopackApp::build().run()` を呼ぶ（導入・削除のフックの処理を含む）。続いて自分が Velopack で入れられたかを確かめ、入れられていれば `data\` の場所を環境変数 `VRCT_DATA_DIR` に入れてからサイドカーを起動する（環境変数はサイドカーに引き継がれる）。Rust の起動ログ（今は実行ファイルの隣の `logs\startup.log`）と WebView2 のデータも同じ `data\` に置く。
- Python は `PATH_LOCAL` を 2 つに分ける:
  - `PATH_APP`: アプリに同梱したもの（実行ファイルのフォルダ）
  - `PATH_DATA`: 設定・ログ・モデル・辞書・AI CLI の作業フォルダなど（`VRCT_DATA_DIR` があればそこ、無ければ実行ファイルのフォルダ）
- 今 `PATH_LOCAL` を使っている 37 か所（`model.py` 29、`config.py` 7、`controller.py` 1）を 1 つずつどちらかに振り分ける。AI CLI の作業フォルダは `PATH_DATAi_cli_workspace` に、プロンプトの設定は `PATH_APP` から読む。
- サイドカーが相対パスで書くログ（`process.log`・`error.log`・`crash_trace.log`・`freeze_trace.log`）は、作業フォルダが `data\` になることで `data\` に入る。
- CUDA の DLL の外部フォルダ（今は元の VRCT の `%LOCALAPPDATA%\VRCT\cudain` を読んでいる）を `VRCT_DATA_DIR\cudain` に移す（元の VRCT のフォルダを読まないため。取得処理はサブプロジェクト 2）。

**リリースの作り方**（`.github/workflows/release.yml` を作り直す）
1. `v*` のタグで動く。版の一致の確認（`package.json`・`tauri.conf.json`・`config.py`）は残す。
2. CPU 版のサイドカーを作り、画面をビルドし、`tauri build --no-bundle` で本体を作る。
3. 本体・サイドカー・`_internal` を 1 つのフォルダにまとめる。
4. 前の版を `vpk download github` で取り、`vpk pack` でパッケージにする（前の版との差分は自動で作られる）。packId `VRCT-0`、main exe `VRCT-0.exe`、アイコン、導入中の画像、`--noPortable`。
5. `vpk upload github` で公開する（ベータ版は `--pre`）。公開されるファイル: `VRCT-0-win-Setup.exe`、`*-full.nupkg`、`*-delta.nupkg`、`releases.win.json`。
6. 2 GiB を超えるファイルが無いかの確認は残す（CPU 版は約 360 MB）。
- CI には .NET SDK と `vpk` を入れる。手元では `npm run release` で 1〜4（公開の手前まで）ができるようにする。
- `release-cuda`・`release-all`・`utils/zip.py` による zip 作成と `VRCT_cuda.zip` はやめる。`backend_cuda.spec` と `.venv_cuda` はサブプロジェクト 2 で CUDA 部品を作るのに使うので残す。

## 5. テストと確認

**自動テスト**
- Rust: データ置き場の決め方（Velopack で入れた場合／開発中）。更新の状態の移り変わり（確認 → ダウンロード中 → 準備完了 → 今すぐ／終了時に適用、と各失敗）を、Velopack との間を差し替えられる形にして偽物で確かめる。ベータ版→安定版の切り替えのときだけ版を下げてよいという判定。
- Python: `PATH_APP` / `PATH_DATA` の決め方（`VRCT_DATA_DIR` の有無）、振り分けた箇所が正しい側を指すこと、削除した更新エンドポイントが UI との契約テスト（`test_ui_endpoint_contract.py`）からも消えていること。
- 画面: `npm run vite-build` が通ること、更新欄の文言が 5 言語すべてにあること。

**実機での確認**（公開の前に、手元の PC で）
1. 手元の `vpk pack` で版を 2 つ作り（例: `3.5.1-beta.1` と `3.5.1-beta.2`）、ローカルのフォルダを更新元にして次を確かめる:
   - `Setup.exe` でページなしに入って起動し、データが `data\` にできること
   - 更新の通知 → 差分のダウンロード → 「今すぐ再起動」と「終了時に更新」の両方で入れ替わること
   - 入れ替えの途中で失敗させても旧版で起動すること
   - 更新しても設定とモデルが残ること
   - 削除すると `%LocalAppData%\VRCT-0` が丸ごと消え、フォルダの外に VRCT-0 のものが残らないこと
   - AI CLI を動かしている最中に削除しても、プロセスもファイルも残らないこと
   - 元の VRCT が入っている場合、それに触れていないこと
2. GitHub Releases からの更新は、公開用の最初のリリースを出すときに確かめる（テスト用のリリースを公開リポジトリに出さないため）。
3. 起動テストのスキル `run-vrct` に、Velopack で入れた版を起動する手順を足す。

## 実装計画の段階で確かめること（2026-09-24 に計画を書く段階で確認した結果）

- `velopack` 1.2.158 の Rust API に `VelopackApp`（削除前のフック `on_before_uninstall_fast_callback`）、`UpdateManager`、`sources::GithubSource::new(repo, token, prerelease)`、`sources::FileSource` がある。Velopack で入れていないと `UpdateManager::new` がエラーを返すので、開発版の判定に使える。
- `vpk pack --framework webview2` は受け付けられる。
- Tauri 2.5.1 は `WebviewWindowBuilder::data_directory` で WebView2 のデータ置き場を指定できる（ウィンドウを `create: false` にして Rust で作る）。
- 残り（入れ替えの失敗時の振る舞い、削除時のプロセスの扱い、Setup の表示言語）は実機での確認で確かめる。

当初の一覧:

- `velopack` クレートを Tauri 2 の `main` に組み込めること（`VelopackApp::build().run()` を Tauri の起動より前に呼ぶ）と、GitHub のソースが Rust の API にあること（C API には `vpkc_new_source_github` がある）。
- `vpk pack --framework` で WebView2 を前提として入れられるか。
- 入れ替えの失敗時に旧版が残るという Velopack の振る舞い。
- Velopack の削除処理が、動いているプロセスをどう扱うか（フックで止める処理が要るか）。
- Tauri 2 で WebView2 のデータ置き場を実行時に指定する方法（ウィンドウを Rust 側で作って `data_directory` を渡す等）。
- 導入中の画面（Velopack の Setup）がどの言語で表示されるか。

## 範囲外

- GPU 部品の後入れ（サブプロジェクト 2）
- 元の VRCT からの取り込み（サブプロジェクト 3）
- コード署名（`vpk pack --signParams` / Azure Trusted Signing で後から足せる）
- PyInstaller の置き換え
- 古い版に戻す機能（ベータ版→安定版の切り替えを除く）
