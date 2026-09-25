# VRCTビルドガイド

このドキュメントでは、VRCTプロジェクトのビルド方法について説明します。

## 目次

- [必要な環境](#必要な環境)
- [初回セットアップ](#初回セットアップ)
- [ビルドの種類](#ビルドの種類)
- [開発ビルド](#開発ビルド)
- [リリースビルド](#リリースビルド)
- [ビルドプロセスの詳細](#ビルドプロセスの詳細)
- [トラブルシューティング](#トラブルシューティング)

## 必要な環境

### 必須ソフトウェア

- **Node.js** (npm含む)
- **Python 3.x**
- **Rust** (Tauri用)
- **Git**

### 推奨環境

- Windows 10/11
- メモリ: 8GB以上
- ストレージ: 5GB以上の空き容量

## 初回セットアップ

### 1. リポジトリのクローン

```bash
git clone <repository-url>
cd VRCT
```

### 2. Node.js依存関係のインストール

```bash
npm install
```

### 3. Python環境のセットアップ

以下のコマンドで、仮想環境を作成します:

```bash
npm run setup-python
```

このコマンドは以下の処理を実行します:
- `.venv` の作成と依存関係のインストール

> **注意**: GPU はアプリの設定から GPU 高速化パックを導入して使います（ダウンロード約 1.3 GB、
> `<データの置き場所>\cuda\bin`）。開発中も同じで、`.venv_cuda` は要りません。

## ビルドの種類

VRCTのビルドは1種類のみです。GPU 対応も含めて同じビルドで動作し、GPU
使用時に必要な cuBLAS / cuDNN はアプリの設定から導入する GPU 高速化パックとして
実行時に取得します。

## 開発ビルド

開発中にアプリケーションを実行・テストするためのビルドです。

### CPU版の開発ビルド

```bash
npm run dev
```

このコマンドは以下を実行します:
1. 実行中のプロセスを終了 (`task-kill`)
2. ビルドファイルのクリーンアップ (`clean`)
3. バージョン情報の更新 (`update-version`)
4. Pythonバックエンドのビルド (`build-python`)
5. ViteとTauriの開発サーバー起動

### UIのみの開発

バックエンドのビルドをスキップして、UIのみを開発する場合:

```bash
npm run dev-ui
```

### 高速開発ビルド（推奨: 日常検証用）

PyInstaller によるバックエンド再パッケージを毎回スキップし、仮想環境の
Python を直接 sidecar として起動する高速ループです。Python コードを
修正した検証も、プロセス再起動だけで反映されます（数分 → 数秒）。

```bash
npm run dev-fast
```

以下を実行します:

1. 実行中のプロセスを終了 (`task-kill`)
2. dev 用 sidecar ラッパー（`utils/dev_sidecar/`, Rust 製の薄いバイナリ）を
   ビルドして `src-tauri/bin/` に配置 (`sidecar-dev`)
3. Vite と Tauri の開発サーバー起動 (`dev-ui`)

前提:

- `.venv/Scripts/python.exe` と依存関係が準備済みであること
- Rust ツールチェーン (`cargo`) が使えること（既に Tauri で必要）

各コマンドが `VRCT_DEV_VENV` を設定し、Tauri 経由で dev 用 sidecar に
使用する環境を渡します。選択した環境が存在しない場合はエラーで停止し、
別の仮想環境への自動切り替えは行いません。環境の有効化（activate）は不要です。

配布 EXE と同一挙動になる根拠:

- Python コード側は `_internal/...` バンドルパスと開発ツリー相対パスの
  両対応が入っており、venv 直起動でもリソース探索が正しく解決される
  （`src-python/models/overlay/overlay_image.py` ほか）
- Tauri 側の sidecar 起動ロジックは共通のまま（EXE ファイル名は不変）

依存ライブラリを追加/更新した場合や、配布形式そのものを確認したい場合は
従来の `npm run dev` / `npm run build` を使ってください。

## リリースビルド

配布用のインストーラー（Velopack）を作成するビルドです。

### 公開する（おすすめ）

```bash
npm run publish-release -- --dry-run     # 何をするかだけ表示する
npm run publish-release                  # 今日の日付で正式版を公開する
npm run publish-release -- --beta 1      # 今日の日付のベータ版 1 を公開する
```

`utils/publish_release.py` が次を順に行います。途中で問題があれば何も push せずに止まります。

1. 公開前の確認: develop ブランチにいる・コミットしていない変更が無い・origin より遅れていない・同じタグが無い・版が公開済みの版より新しい
2. テスト: Python のテストと UI のビルド（`--skip-tests` で省く）
3. 版（package.json など）と `BUILD_CHANNEL` を書き換えて `chore(release): v<版>` をコミットし、タグ `v<版>` を作る
4. 確認のあと、develop とタグを一度に push する（`--yes` で確認を省く）
5. CI の完了を待ち、公開されたリリースに Setup・nupkg・releases.win.json があるかを確かめる（`--no-watch` で待たない）

CI が失敗したときは、原因を直してから、タグを消して（`git push --delete origin v<版>` と `git tag -d v<版>`）
もう一度 `npm run publish-release` を実行します。版のコミットはもうあるので、作り直さずにタグだけを付け直します。

### CI の中身

タグ `v<版>` を push すると CI（`.github/workflows/release.yml`）が
`npm run build` → `vpk pack`（`utils/pack_release.py`）→ `vpk upload github` を実行し、
GitHub Releases に `VRCT-0-win-Setup.exe` と差分パッケージ（nupkg）を公開します。

手元で作る場合:

```bash
npm run release
```

`vpk` が入っていない場合は先に入れてください:

```bash
dotnet tool install -g vpk --version 1.2.158
```

生成されるファイル（`release/velopack/`）:
- `VRCT-0-win-Setup.exe`
- `VRCT-0-<版>-full.nupkg`（前の版の full.nupkg が同じ場所にあれば `-delta.nupkg` も）
- `releases.win.json`

## ビルドプロセスの詳細

### バージョン管理

VRCT-0 の版は**リリースした日付**（年.月.日）です。ベータ版は後ろに `-beta.<番号>` を付けます。

- 例: `2026.9.25`（安定版）、`2026.9.25-beta.1`（ベータ版）
- 更新の仕組み（Velopack）は SemVer の版しか受け付けないので、月と日はゼロで埋めません（`2026.09.25` は使えません）。
- 数字は 3 つまでなので、同じ日に 2 回目の安定版は出せません。続けて直すときはベータ版（`2026.9.25-beta.2`）で出すか、翌日の日付で出します。

版を決めるコマンド:

```bash
npm run set-version                 # 今日の日付を版にする (安定版)
npm run set-version -- --beta 1     # 今日の日付のベータ版 1
python utils/update_version.py --date 2026-09-25 --beta 2   # 日付を指定する
```

`package.json` と `package-lock.json` の版を書き換え、以下のファイルにも同期します。
日付の形でない版のままビルドすると `update-version` が止まります。

- `src-tauri/tauri.conf.json`
- `src-python/config.py`

`package.json` の版をほかのファイルへ写すだけなら:

```bash
npm run update-version
```

### どこにバージョンを設定すればReleaseに反映されるか

- **設定箇所**: `package.json` の `version` が唯一のソース・オブ・トゥルース（`npm run set-version` で日付の版にする）。
- **反映方法**: `npm run update-version`（`build`/`release`コマンド内でも自動実行）により、
	- `src-tauri/tauri.conf.json` の `version` に同期（Tauri アプリの表示・メタデータに使用）
	- `src-python/config.py` の `self._VERSION` に同期（ランタイム表示等に使用）
- **成果物への影響**:
	- `utils/pack_release.py` は `package.json` の `version` を読んで Velopack の packVersion に使うため、`VRCT-0-win-Setup.exe` とファイル名がそのバージョンになります。

### Pythonバックエンドのビルド

```bash
npm run build-python
```

実行内容:

- `.venv` 環境をアクティベート
- PyInstallerで `spec/backend.spec` を使用してビルド
- 出力先: `src-tauri/bin/`

### フロントエンドのビルド

```bash
npm run vite-build
```

Viteを使用してフロントエンド（React）をビルドし、`dist/` ディレクトリに出力します。

### Tauriアプリケーションのビルド

```bash
npm run tauri build
```

Tauriを使用して最終的なデスクトップアプリケーションをビルドします。

## GitHub ActionsでのRelease自動化

`.github/workflows/release.yml` がタグ `v*` の push をトリガーに、版と `BUILD_CHANNEL` の確認、
`npm run build`、`vpk pack`（`utils/pack_release.py`）、`vpk upload github` までを実行し、
GitHub Releases に Velopack のパッケージを公開します。ベータ版（タグに `-beta`/`-rc` を含む）は
プレリリースとして扱われます。手順の詳細は同ファイルを参照してください。

## ユーティリティコマンド

### クリーンアップ

```bash
npm run clean
```

以下のディレクトリを削除します:
- `build/`
- `dist/`
- `src-tauri/bin/`
- `src-tauri/target/`

Rust のインクリメンタルビルド結果 (`src-tauri/target/`) を残したい場合は
`--soft` 版を使ってください:

```bash
npm run clean-soft
```

こちらは `build/`, `dist/`, `src-tauri/bin/` のみを削除します。

### PyInstaller のオプション環境変数

`build-python` は以下の環境変数で挙動を切り替えられます:

- `VRCT_PYINSTALLER_CLEAN=1` — PyInstaller に `--clean` を渡し、
  Analysis キャッシュを破棄してからビルド（依存追加時・リリース前確認用）。
  既定では付与しないので、2 回目以降のビルドは Analysis キャッシュが効いて
  数倍速くなります。
- `VRCT_PYINSTALLER_UPX=1` — 生成物を UPX で圧縮（配布サイズを最小化したい
  リリース時のみ）。既定は無効。UPX 圧縮はビルド時間と初回起動時間の両方を
  伸ばすため、開発中は無効を推奨します。

### プロセスの強制終了

```bash
npm run task-kill
```

VRCTに関連する実行中のプロセスを終了します。

## ディレクトリ構成

```
VRCT/
├── bat/                    # バッチスクリプト
│   ├── build.bat          # Pythonビルド
│   ├── install.bat        # Python環境セットアップ
│   └── sidecar_dev.bat    # dev-fast用sidecarラッパービルド
├── spec/                   # PyInstallerスペックファイル
│   └── backend.spec       # ビルド設定
├── src-python/            # Pythonバックエンドソースコード
├── src-tauri/             # Tauriアプリケーション設定
│   ├── bin/              # ビルド済みPythonバイナリ（生成）
│   └── target/           # Tauriビルド出力（生成）
├── src-ui/               # Reactフロントエンドソースコード
├── utils/                # ユーティリティスクリプト
│   ├── clean.py         # クリーンアップスクリプト
│   ├── dev_sidecar/     # dev-fast用のvenv直起動ラッパー(Rust)
│   ├── task_kill.py     # プロセス終了スクリプト
│   ├── update_version.py # バージョン更新スクリプト
│   └── pack_release.py  # Velopack パッケージング
├── package.json          # Node.js設定とバージョン管理
└── requirements.txt      # Python依存関係
```

## トラブルシューティング

### Python環境のエラー

仮想環境を再作成してください:

```bash
npm run setup-python
```

### ビルドが失敗する

1. クリーンアップを実行:
```bash
npm run clean
```

2. Node.js依存関係を再インストール:
```bash
npm install
```

3. 再度ビルド:
```bash
npm run build
```

### GPUが動作しない

- NVIDIA GPUドライバーが最新か確認（CUDA Toolkit のインストールは不要）
- アプリの設定から GPU 高速化パック（約 1.3 GB）を導入し、再起動したか確認
- `<データの置き場所>\cuda\bin\` にDLLがあるか確認。ctranslate2 はGPU実行時に
  ここの `cublas64_12.dll` / `cudnn64_9.dll` を実行時ロードする。無ければ
  GPUは計算デバイス一覧に出ない

### プロセスが残っている

```bash
npm run task-kill
```

を実行して、すべてのVRCTプロセスを終了してください。

## 参考情報

### PyInstallerスペックファイル

- `spec/backend.spec` - ビルド設定

このファイルでは、以下を設定しています:
- エントリーポイント: `src-python/mainloop.py`
- データファイル（フォント、プロンプト、言語ファイル等）のパス
- 依存ライブラリのパス

### バージョン管理フロー

1. `npm run set-version`（ベータ版は `npm run set-version -- --beta <番号>`）で `package.json` の版をリリースする日付にする
2. 同じコマンドの中で `tauri.conf.json` と `config.py` も更新される
3. 変更をコミットして、`v<版>` のタグを打つ

## β版リリース

実際のリリースは `.github/workflows/release.yml` が `v*` タグのpushで自動実行しますが、
タグ名に `-beta` または `-rc` を含めることで、公開先を本番と分離したβ版として配布できます。

### リリース手順

1. `npm run set-version -- --beta 1` で版を `2026.9.25-beta.1` のような今日の日付のベータ版にし、コミットする
2. `v2026.9.25-beta.1` のようなタグを打ってpush（CI はタグが日付の形か、各ファイルの版とタグが同じかを確かめる）

```bash
git tag v2026.9.25-beta.1
git push origin v2026.9.25-beta.1
```

安定版は `npm run set-version`（`-beta` なし）で版を今日の日付にし、`src-python/build_channel.py` の
`BUILD_CHANNEL` を `"stable"` にしてから `v2026.9.25` のようなタグを打ちます。

### 本番版との違い

| 項目 | 本番版 (`v2026.9.25`) | β版 (`v2026.9.25-beta.1`) |
|---|---|---|
| GitHub Release | `prerelease: false` | `prerelease: true` |

このフォークでは配布元は常にこのリポジトリ(`lighfu/VRCT-0`)の GitHub
Releases 一本のみで、本番/β版で公開先を分けることはしません(旧 upstream の
Hugging Face 2リポジトリ構成は廃止)。

### チャンネル切り替え・旧バージョンへのロールバック

更新は Velopack ベースで、アプリを起動した状態のまま行います。コマンドライン
引数でバージョンを指定する仕組みはもう存在しません(旧 NSIS 版の
`/VERSION=` / `/CHANNEL=` は廃止)。

- アプリ内の設定 → アップデートから、安定版 (stable) / ベータ版 (beta) の
  チャンネルを選べます。
- ベータ版チャンネルでは GitHub のプレリリースも更新対象になります。
- チャンネルを「安定版」に切り替えると、今の版がプレリリースであっても
  最新の安定版へ更新します(バージョンを下げる形になっても構いません)。
  それ以外のケースでバージョンを下げることはありません。
- 更新の確認・ダウンロードはアプリが行い、適用は「今すぐ再起動」または
  「次に閉じたときに入れ替え」のどちらかをユーザーが選びます。

Velopack の Setup.exe 自体は `-s`(サイレントインストール)、
`--installto <導入先ディレクトリ>` などのオプションを受け付けます(詳細は
`Setup.exe --help`)。

入れてある上から `VRCT-0-win-Setup.exe` を実行すると、Velopack は版に応じて
「修復」(同じ版)・「更新」(新しい版)・「ダウングレード」(古い版。Velopack は
勧めていない)を尋ね、導入先を丸ごと `%LocalAppData%\VRCT-0.<英数字16文字>` に
退避してから入れ直し、終わると退避したフォルダを消します。そのままでは
`data\`(設定・API キー・単語の一覧・モデル)も消えるので、VRCT-0 は導入のフック
(`--veloapp-install`)で退避したフォルダから `data\` を戻します
(`src-tauri/src/reinstall.rs`。2026-09-24 に 3 通りとも実機で確認)。
戻すのは入れる側の版の `VRCT-0.exe` なので、この処理が入る前の版の Setup で
上書きすると `data\` は消えます(公開した版にはすべて入っています)。
また、フックのあとで Setup 自体が失敗して元に戻すとき(アンインストールの登録や
アプリの起動に失敗したとき)は、Velopack が新しい導入先ごと消すので、戻した
`data\` も消えます。古い版に戻したいときは、先に `data\` を別の場所へ写してから
Setup を実行してください。

Velopack の GitHub の更新元(`GithubSource`)は、GitHub Releases の新しいほうから
10 件だけを読み、そのあとでプレリリースを除きます。安定版を出さないまま
ベータ版を 10 回以上出すと、安定版チャンネルの人(とベータ版から安定版に切り替えた人)
には安定版が見えなくなり、「最新版です」と表示されます。ベータ版を続けて出すときは、
古いプレリリースを消すか、間に安定版を出してください。

### アンインストール

Windows の 設定 → アプリ から `VRCT-0` をアンインストールします。
`%LocalAppData%\VRCT-0` 以下(アプリ本体・データとも)がまとめて削除されます。
Setup が消し損ねた退避フォルダ(`%LocalAppData%\VRCT-0.<英数字16文字>`)があれば、
それも削除の直前のフックで消します。Velopack 自身のログ
(`%LocalAppData%\velopack\velopack_VRCT-0.log` など)は残ります。

### リリースパッケージの内容

`utils/pack_release.py` が Velopack 用にステージするファイル:
- `VRCT-0.exe` - メインアプリケーション
- `VRCT-sidecar.exe` - Pythonバックエンド
- `_internal/` - 必要な依存ファイル

## ライセンス

プロジェクトのライセンスについては、`LICENSE` ファイルを参照してください。
