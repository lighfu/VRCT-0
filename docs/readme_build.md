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

以下のコマンドで、CPU版とCUDA版の両方の仮想環境を作成します:

```bash
npm run setup-python
```

このコマンドは以下の処理を実行します:
- `.venv` (CPU版) の作成と依存関係のインストール
- `.venv_cuda` (CUDA版) の作成と依存関係のインストール

> **注意**: CUDA版を使用する場合は、CUDA 12.8対応のNVIDIA GPUドライバーが必要です。
> CUDA Toolkit のインストールは不要です。ctranslate2 が使う cuBLAS / cuDNN は
> `requirements_cuda.txt` の `nvidia-*-cu12` wheel で入り、ビルド時に同梱されます。

## ビルドの種類

VRCTでは、以下の2種類のビルドが可能です:

### CPU版
標準的なCPUで動作するバージョン。GPUは不要。

### CUDA版
NVIDIA GPUを活用した高速処理版。CUDA対応GPUが必要。

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

### CUDA版の開発ビルド

```bash
npm run dev-cuda
```

CPU版と同様ですが、CUDA対応のPythonバックエンドをビルドします。

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
# 標準環境: .venv/Scripts/python.exe
npm run dev-fast

# CUDA環境: .venv_cuda/Scripts/python.exe
npm run dev-cuda-fast
```

どちらのコマンドも以下を実行します:

1. 実行中のプロセスを終了 (`task-kill`)
2. dev 用 sidecar ラッパー（`utils/dev_sidecar/`, Rust 製の薄いバイナリ）を
   ビルドして `src-tauri/bin/` に配置 (`sidecar-dev`)
3. Vite と Tauri の開発サーバー起動 (`dev-ui`)

前提:

- 選択した仮想環境と依存関係が準備済みであること
  - `dev-fast`: `.venv/Scripts/python.exe`
  - `dev-cuda-fast`: `.venv_cuda/Scripts/python.exe`（`.venv` は不要）
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

バージョンは `package.json` で一元管理され、以下のファイルに自動で同期されます:

```bash
npm run update-version
```

更新されるファイル:
- `src-tauri/tauri.conf.json`
- `src-python/config.py`

### どこにバージョンを設定すればReleaseに反映されるか

- **設定箇所**: `package.json` の `version` が唯一のソース・オブ・トゥルース。
- **反映方法**: `npm run update-version`（`build`/`release`コマンド内でも自動実行）により、
	- `src-tauri/tauri.conf.json` の `version` に同期（Tauri アプリの表示・メタデータに使用）
	- `src-python/config.py` の `self._VERSION` に同期（ランタイム表示等に使用）
- **成果物への影響**:
	- `utils/pack_release.py` は `package.json` の `version` を読んで Velopack の packVersion に使うため、`VRCT-0-win-Setup.exe` とファイル名がそのバージョンになります。

### Pythonバックエンドのビルド

#### CPU版

```bash
npm run build-python
```

実行内容:

- `.venv` 環境をアクティベート
- PyInstallerで `spec/backend.spec` を使用してビルド
- 出力先: `src-tauri/bin/`

#### CUDA版

```bash
npm run build-python-cuda
```

実行内容:

- `.venv_cuda` 環境をアクティベート
- PyInstallerで `spec/backend_cuda.spec` を使用してビルド
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

`build-python` / `build-python-cuda` は以下の環境変数で挙動を切り替えられます:

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
│   ├── build.bat          # CPU版Pythonビルド
│   ├── build_cuda.bat     # CUDA版Pythonビルド
│   ├── install.bat        # Python環境セットアップ
│   └── sidecar_dev.bat    # dev-fast用sidecarラッパービルド
├── spec/                   # PyInstallerスペックファイル
│   ├── backend.spec       # CPU版ビルド設定
│   └── backend_cuda.spec  # CUDA版ビルド設定
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
├── requirements.txt      # Python依存関係（CPU版）
└── requirements_cuda.txt # Python依存関係（CUDA版。requirements.txt + CUDAライブラリ）
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

### CUDA版が動作しない

- NVIDIA GPUドライバーが最新か確認（CUDA Toolkit のインストールは不要）
- `requirements_cuda.txt` の依存関係が正しくインストールされているか確認
- `.venv_cuda/Lib/site-packages/nvidia/{cublas,cudnn}/bin/` にDLLがあるか確認。
  ctranslate2 はGPU実行時にここの `cublas64_12.dll` / `cudnn64_9.dll` を
  実行時ロードする。無ければGPUは計算デバイス一覧に出ない

### プロセスが残っている

```bash
npm run task-kill
```

を実行して、すべてのVRCTプロセスを終了してください。

## 参考情報

### PyInstallerスペックファイル

- `spec/backend.spec` - CPU版の設定
- `spec/backend_cuda.spec` - CUDA版の設定

これらのファイルでは、以下を設定しています:
- エントリーポイント: `src-python/mainloop.py`
- データファイル（フォント、プロンプト、言語ファイル等）のパス
- 依存ライブラリのパス

### バージョン管理フロー

1. `package.json` のバージョンを更新
2. `npm run update-version` を実行
3. 自動的に `tauri.conf.json` と `config.py` が更新される

## β版リリース

実際のリリースは `.github/workflows/release.yml` が `v*` タグのpushで自動実行しますが、
タグ名に `-beta` または `-rc` を含めることで、公開先を本番と分離したβ版として配布できます。

### リリース手順

1. `package.json` の `version` を `3.5.0-beta.1` のようなpre-release識別子付きの値にする
2. `npm run update-version` を実行(通常のビルド/リリースコマンド内で自動実行されるため手動実行は不要)
3. `v3.5.0-beta.1` のようなタグを打ってpush

```bash
git tag v3.5.0-beta.1
git push origin v3.5.0-beta.1
```

### 本番版との違い

| 項目 | 本番版 (`v3.5.0`) | β版 (`v3.5.0-beta.1`) |
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
