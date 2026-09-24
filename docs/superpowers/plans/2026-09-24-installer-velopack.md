# インストーラー（基盤）— Velopack への移行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NSIS のダウンロード型インストーラーと Python 側の更新処理をやめ、Velopack でワンクリック導入・差分更新（旧版が壊れない）・丸ごと削除ができるようにする。

**Architecture:** Tauri 本体（Rust）が起動の最初に `VelopackApp` を動かし、Velopack で入れた版なら `<導入先>\data` をデータの置き場所として環境変数 `VRCT_DATA_DIR` と作業フォルダでサイドカーに渡す。更新は Rust の `Updater`（Velopack の `UpdateManager` を包む）が確認・ダウンロードし、アプリを閉じるときに入れ替えを頼む。画面は Tauri のコマンドとイベントで更新役とやりとりする。リリースは CI で `vpk pack` / `vpk upload github` を使う。

**Tech Stack:** Rust（Tauri 2.5.1、`velopack` クレート 1.2.158）、Python 3.11（サイドカー、pytest）、React 18 + jotai（画面）、Velopack CLI `vpk` 1.2.158（.NET ツール）、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-09-24-installer-velopack-design.md`

## Global Constraints

- 表示名 `VRCT-0`、Tauri の identifier `com.lighfu.vrct0`、本体の実行ファイル `VRCT-0.exe`（Cargo のパッケージ名 `VRCT-0`）、Velopack の packId `VRCT-0`。サイドカーの名前は `VRCT-sidecar` のまま。
- 導入先 `%LocalAppData%\VRCT-0\`、データの置き場所 `<導入先>\data`、サイドカーへ渡す環境変数 `VRCT_DATA_DIR`。
- Velopack で入れた版は導入先の外に何も書かない（WebView2 のデータは `data\webview`、起動ログは `data\logs\startup.log`）。
- 元の VRCT のフォルダ `%LOCALAPPDATA%\VRCT` を読まない・書かない。
- 更新元は GitHub Releases `https://github.com/lighfu/VRCT-0`。ベータ版チャンネルは GitHub のプレリリースも対象にする。版を下げてよいのは「安定版チャンネル かつ 今の版がプレリリース」のときだけ。
- 画面へ送るイベント名 `app-update://state`。Tauri のコマンド名 `updater_state` / `updater_check` / `updater_download` / `updater_restart_now`。
- 更新は「通知 → 押したらダウンロード → 再起動（今すぐ）／次に閉じたとき入れ替え」。勝手に再起動しない。
- `velopack` クレートは `=1.2.158`、`vpk` も 1.2.158 を使う。
- この計画では公開リリースを出さない（GitHub への公開はサブプロジェクト 2 のあと）。
- 画面の文言は 5 言語（ja / en / ko / zh-Hant / zh-Hans）すべてに入れる。新しい文言は ja が日本語、ほかは英語。
- テスト: `.venv\Scripts\python -m pytest -q`（-k で絞らない全体）、`cargo test --manifest-path src-tauri/Cargo.toml`（cargo は `$env:USERPROFILE\.cargo\bin` を PATH に足す）、`npm run vite-build`。`npm run vite-build` で `package-lock.json` の先頭の `"version"` が変わったら戻す。
- コードの調査は CodeGraph（`codegraph_*`）から始める。grep は文字列そのものを探すときだけ。
- コミットメッセージは日本語で、末尾に次の 2 行を付ける:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01DWzgQaydpNgp2mJ9rSGipp`

## Review Focus

1. **ベータ版の Setup で初めて入れた人（config.json が無い）** — チャンネルの既定値がベータ版になり、起動直後に「安定版へ戻す」更新を勧めないこと。→ Task 1 の `test_channel_defaults_to_the_channel_of_this_version`。
2. **通信できない・GitHub の回数制限に当たったとき** — 起動時の確認は黙って `idle` に戻り、「更新を確認」を押したときだけ失敗を表示し、アプリはそのまま使えること。→ Task 4 の `automatic_check_failure_is_silent` / `manual_check_failure_is_shown`。
3. **アプリや AI CLI が動いたまま削除したとき** — `current\` から動いているプロセスだけを止め、削除を進めている `Update.exe`（導入先の直下）とフック自身は止めないこと。導入先のパスに `'` や日本語が入っていても壊れないこと。→ Task 3 の `stop_script_*` テスト。
4. **サイドカーが相対パスで書くログ（process.log・error.log・crash_trace.log・freeze_trace.log）** — Velopack で入れた版では更新で消える `current\` ではなく `data\` に入ること。→ Task 3 の `prepare_sets_env_and_working_dir`。
5. **ダウンロード中や準備完了のあとに、もう一度確認やダウンロードを押したとき** — ダウンロードをやり直さず、準備完了の状態を壊さないこと。→ Task 4 の `check_is_ignored_while_downloading_or_ready` / `second_download_is_ignored`。

---

### Task 1: Python の更新処理と NSIS 由来の処理を消し、初回の既定値を決める

**Files:**
- Modify: `src-python/config.py`
- Modify: `src-python/model.py`
- Modify: `src-python/controller.py`
- Modify: `src-python/mainloop.py`
- Delete: `src-python/test/test_model_update.py`、`src-python/test/test_model_http_timeouts.py`、`src-python/test/test_config_github_release_source.py`、`src-python/test/test_config_release_channel_self_heal.py`
- Modify: `src-python/test/test_controller_init_config_settings.py`、`src-python/test/test_endpoints.py`、`src-python/test/test_client.py`
- Modify: `src-python/docs/config.md`、`src-python/docs/model.md`（setup.exe の取得・更新処理の説明を削る）
- Create: `src-python/test/test_config_first_run_defaults.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `config.uiLanguageForLocale(name: str) -> str`、`config.osUiLanguage() -> str`（モジュール関数）
  - `Config._channelForVersion(version: str) -> str`（残す）
  - `SELECTED_RELEASE_CHANNEL` と `/get|set/data/release_channel` は残る（画面が Task 5 で使う）。`VERSION` と `/get/data/version`、`COMPUTE_MODE` と `/get/data/compute_mode` も残る。
  - 消えるエンドポイント: `/get/data/available_releases`、`/run/update_software`、`/run/update_cuda_software`、run_mapping の `software_update_info`（`/run/software_update_info`）。

- [ ] **Step 1: 初回の既定値のテストを書く**

`src-python/test/test_config_first_run_defaults.py`:

```python
"""初回起動時の既定値 (リリースチャンネル・UI 言語) のテスト。

NSIS インストーラーが置いていた installer_language.txt と、起動のたびに
チャンネルを VERSION から上書きする処理をやめた。代わりに config.json が
無いときの既定値を、チャンネルは VERSION から、UI 言語は OS の表示言語から
決める。保存済みの値は上書きしない。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config as config_module
from config import Config


def _isolated_config() -> Config:
    """シングルトン (Config.__new__) を通らない、独立した Config を作る。"""
    instance = object.__new__(Config)
    instance.init_config()
    return instance


def _cancel_timer(instance: Config) -> None:
    timer = getattr(instance, "_timer", None)
    if timer is not None:
        timer.cancel()


class UiLanguageForLocaleTests(unittest.TestCase):
    def test_known_locales(self) -> None:
        cases = {
            "ja_JP": "ja",
            "ko_KR": "ko",
            "zh_TW": "zh-Hant",
            "zh_HK": "zh-Hant",
            "zh_CN": "zh-Hans",
            "zh_SG": "zh-Hans",
            "en_US": "en",
            "de_DE": "en",
            "": "en",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(config_module.uiLanguageForLocale(name), expected)


class FirstRunDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.json"

    def _load(self, saved):
        if saved is not None:
            self.config_path.write_text(json.dumps(saved), encoding="utf-8")
        with patch.object(config_module, "osUiLanguage", return_value="ja"):
            instance = _isolated_config()
        instance._PATH_CONFIG = str(self.config_path)
        instance.load_config()
        self.addCleanup(_cancel_timer, instance)
        return instance

    def test_ui_language_defaults_to_the_os_language(self) -> None:
        self.assertEqual(self._load(None).UI_LANGUAGE, "ja")

    def test_saved_ui_language_wins(self) -> None:
        self.assertEqual(self._load({"UI_LANGUAGE": "ko"}).UI_LANGUAGE, "ko")

    def test_channel_defaults_to_the_channel_of_this_version(self) -> None:
        instance = self._load(None)
        self.assertEqual(
            instance.SELECTED_RELEASE_CHANNEL,
            Config._channelForVersion(instance.VERSION),
        )

    def test_saved_channel_is_not_overwritten(self) -> None:
        self.assertEqual(self._load({"SELECTED_RELEASE_CHANNEL": "stable"}).SELECTED_RELEASE_CHANNEL, "stable")
        self.assertEqual(self._load({"SELECTED_RELEASE_CHANNEL": "beta"}).SELECTED_RELEASE_CHANNEL, "beta")

    def test_channel_for_version(self) -> None:
        self.assertEqual(Config._channelForVersion("3.5.1"), "stable")
        self.assertEqual(Config._channelForVersion("3.5.1-beta.1"), "beta")
        self.assertEqual(Config._channelForVersion("3.5.1-rc.2"), "beta")
```

- [ ] **Step 2: 失敗を確かめる**

Run: `.venv\Scripts\python -m pytest src-python/test/test_config_first_run_defaults.py -q`
Expected: FAIL（`uiLanguageForLocale` / `osUiLanguage` が無い。`test_saved_channel_is_not_overwritten` は今の起動時の上書きで `beta` が `stable` に戻され失敗する）

- [ ] **Step 3: config.py を直す**

1. `class Config` より前（モジュールの先頭の import のあと）に追加:

```python
def uiLanguageForLocale(name: str) -> str:
    """ロケール名 (例: "ja_JP"、"zh_TW") を VRCT の UI 言語コードにする。当てはまらなければ英語。"""
    lowered = (name or "").lower().replace("-", "_")
    if lowered.startswith("ja"):
        return "ja"
    if lowered.startswith("ko"):
        return "ko"
    if lowered.startswith(("zh_tw", "zh_hk", "zh_mo")) or "hant" in lowered:
        return "zh-Hant"
    if lowered.startswith("zh"):
        return "zh-Hans"
    return "en"


def osUiLanguage() -> str:
    """Windows の表示言語から UI 言語を決める。config.json が無い初回起動の既定値に使う。"""
    try:
        import ctypes
        import locale
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return uiLanguageForLocale(locale.windows_locale.get(lang_id, ""))
    except Exception:
        return "en"
```

2. 消す: `SOFTWARE_RELEASE_GITHUB_REPO`、`setupDownloadUrlForTag`、`setupDownloadUrlForVersion`、ディスクリプタ `GITHUB_URL` / `GITHUB_RELEASES_LIST_URL` / `MIN_SUPPORTED_VERSION` と `init_config` の中のその値（`_GITHUB_URL`、`_GITHUB_RELEASES_LIST_URL`、3.4.3 の `_MIN_SUPPORTED_VERSION` とそのコメント）。
3. `_RELEASE_CHANNEL_BETA_MARKERS` と `_channelForVersion` は残し、コメントと docstring から NSIS の話を消して「VERSION に含まれていれば beta チャンネルとする接尾辞。config.json が無いときのチャンネルの既定値に使う」とする。
4. `init_config` の `self._SELECTED_RELEASE_CHANNEL = "stable"` を次に変える:

```python
        # 初回 (config.json に値が無いとき) は、この版のチャンネルを既定にする。
        # ベータ版の Setup で入れた人に、起動直後から安定版へ戻す更新を勧めないため。
        self._SELECTED_RELEASE_CHANNEL = self._channelForVersion(self._VERSION)
```

5. `init_config` の `self._UI_LANGUAGE = "en"` を `self._UI_LANGUAGE = osUiLanguage()` に変える。
6. `load_config` の末尾から、チャンネルの上書き（`self.SELECTED_RELEASE_CHANNEL = self._channelForVersion(self.VERSION)` とその前のコメント）と、`installer_language.txt` を読む処理（`installer_language_marker` の塊）を消す。`self.saveConfigToFile()` は残す。`os_remove` の import が使われなくなったら消す。

- [ ] **Step 4: model.py・controller.py・mainloop.py から更新処理を消す**

- `model.py`: `ReleaseInfo`、`SetupSha256Unavailable`、更新だけが使う `_HTTP_TIMEOUT`、`_isVersionSupported`、`_fetchGithubReleases`、`checkSoftwareUpdated`、`listAvailableReleases`、`_SHA256_*`、`_resolveReleaseForVersion`、`_fetchExpectedSha256`、`_fetchSha256Digest`、`_downloadSetup`、`_SETUP_ASSET_NAME`、`_downloadVerifiedSetup`、`updateSoftware`、`updateCudaSoftware`、`_quitApp` を消す。使われなくなる import（`hashlib`、`os_getppid`、`os_exit`、`psutil_Process`、`requests_get`、`parse`、`dataclass` のうち他で使っていないもの）を消す。消す前に各名前を CodeGraph と grep で探し、他の呼び出し元が無いことを確かめる。
- `controller.py`: `checkSoftwareUpdated`、`listAvailableReleases`（と `asdict` の import）、`updateSoftware` / `updateCudaSoftware` のスレッド、起動時の `check_software_updated_background` スレッドを消す。
- `mainloop.py`: run_mapping の `"software_update_info"`、mapping の `/get/data/available_releases`、`/run/update_software`、`/run/update_cuda_software` を消す。`_INIT_MAPPING_EXCLUDED_ENDPOINTS` とそのコメントを消し、`init_mapping` の条件を `key.startswith("/get/data/")` だけにする。
- `src-python/docs/config.md` と `model.md` から、消した関数・setup.exe の取得の説明を削る。

- [ ] **Step 5: 古いテストを消し、残るテストを直す**

- 消す: `test_model_update.py`、`test_model_http_timeouts.py`、`test_config_github_release_source.py`、`test_config_release_channel_self_heal.py`。
- `test_endpoints.py` から `/run/update_software` と `/run/update_cuda_software` のケースを、`test_client.py` の一覧から同じ 2 つと `/get/data/available_releases` を消す。
- `test_controller_init_config_settings.py` の `TestInitMappingExcludesNetworkEndpoints` を次に置き換える:

```python
class TestInitMappingCollectsAllGetData(unittest.TestCase):
    def test_every_get_data_endpoint_is_collected_at_startup(self) -> None:
        get_data_keys = {k for k in mapping if k.startswith("/get/data/")}
        self.assertEqual(get_data_keys - set(init_mapping), set())

    def test_update_endpoints_are_gone(self) -> None:
        for endpoint in (
            "/get/data/available_releases",
            "/run/update_software",
            "/run/update_cuda_software",
        ):
            self.assertNotIn(endpoint, mapping)
```

- [ ] **Step 6: 全テストを流す**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗 0。件数は 1144 から消したテストの分だけ減り、新しいテスト分だけ増える（報告に件数を書く）。

- [ ] **Step 7: Commit**

```powershell
git add -A src-python
git commit -m "refactor(update): Python 側の更新処理と NSIS 由来の処理を消し、初回の言語とチャンネルを決める"
```

---

### Task 2: データの置き場所を分ける（PATH_APP / PATH_DATA）

**Files:**
- Modify: `src-python/utils.py`（`dataDirectory`、`DATA_DIR_ENV`、`externalCudaLibraryDir`）
- Modify: `src-python/config.py`（`PATH_LOCAL` → `PATH_APP` / `PATH_DATA`）
- Modify: `src-python/model.py`、`src-python/controller.py`（`PATH_LOCAL` の全箇所）
- Modify: `src-python/models/translation/translation_translator.py`（`checkAiCliClient` に `workspace`）
- Modify: `src-python/test/test_utils_cuda_library_dirs.py`、`src-python/test/test_translation_ct2_tokenizer.py`
- Modify: `src-python/docs/config.md`、`src-python/docs/telemetry_design.md`、`src-python/docs/details/translation_ai_cli.md`（`PATH_LOCAL` の記述）
- Create: `src-python/test/test_config_data_dir.py`、`src-python/test/test_model_ai_cli_workspace.py`

**Interfaces:**
- Consumes: Task 1 の config.py
- Produces:
  - `utils.DATA_DIR_ENV = "VRCT_DATA_DIR"`、`utils.dataDirectory(app_dir: str) -> str`
  - `config.PATH_APP`（同梱ファイルの場所 = 実行ファイルのフォルダ、開発中は `src-python`）、`config.PATH_DATA`（`VRCT_DATA_DIR` があればそこ、無ければ `PATH_APP`）。`PATH_CONFIG = PATH_DATA\config.json`、`PATH_LOGS = PATH_DATA\logs`。
  - `utils.externalCudaLibraryDir()` は `VRCT_DATA_DIR\cuda\bin`（存在すれば）を返す。`%LOCALAPPDATA%\VRCT` は見ない。
  - `Translator.checkAiCliClient(tool, root_path=None, client_version="", workspace=None)`

- [ ] **Step 1: テストを書く**

`src-python/test/test_config_data_dir.py`:

```python
"""データの置き場所 (PATH_DATA) と同梱ファイルの場所 (PATH_APP) のテスト。

Velopack で入れた版では本体 (VRCT-0.exe) が <導入先>\\data を環境変数
VRCT_DATA_DIR で渡す。設定・ログ・モデルはそこへ、同梱ファイルは
実行ファイルのフォルダから読む。開発中は両方とも今までどおり。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils
from config import Config

SRC_PYTHON = Path(__file__).resolve().parents[1]


def _isolated_config() -> Config:
    instance = object.__new__(Config)
    instance.init_config()
    return instance


def _env_without_data_dir() -> dict:
    return {k: v for k, v in os.environ.items() if k != utils.DATA_DIR_ENV}


class DataDirectoryTests(unittest.TestCase):
    def test_env_var_is_used_when_set(self) -> None:
        with patch.dict(os.environ, {utils.DATA_DIR_ENV: r"D:\somewhere\data"}):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"D:\somewhere\data")

    def test_app_dir_is_used_without_env_var(self) -> None:
        with patch.dict(os.environ, _env_without_data_dir(), clear=True):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"C:\app")

    def test_blank_env_var_is_ignored(self) -> None:
        with patch.dict(os.environ, {utils.DATA_DIR_ENV: "  "}):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"C:\app")


class ConfigPathsTests(unittest.TestCase):
    def test_installed_layout_puts_config_and_logs_in_data(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {utils.DATA_DIR_ENV: data_dir}):
                instance = _isolated_config()
            self.assertEqual(instance.PATH_DATA, data_dir)
            self.assertEqual(instance.PATH_CONFIG, os.path.join(data_dir, "config.json"))
            self.assertEqual(instance.PATH_LOGS, os.path.join(data_dir, "logs"))
            self.assertTrue(os.path.isdir(instance.PATH_LOGS))
        self.assertEqual(Path(instance.PATH_APP), SRC_PYTHON)

    def test_dev_layout_keeps_everything_next_to_the_code(self) -> None:
        with patch.dict(os.environ, _env_without_data_dir(), clear=True):
            instance = _isolated_config()
        self.assertEqual(instance.PATH_DATA, instance.PATH_APP)


class NoPathLocalLeftTests(unittest.TestCase):
    def test_path_local_is_gone_from_the_code(self) -> None:
        offenders = []
        for path in SRC_PYTHON.rglob("*.py"):
            if "test" in path.parts or "__pycache__" in path.parts:
                continue
            if "PATH_LOCAL" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(SRC_PYTHON)))
        self.assertEqual(offenders, [])
```

`src-python/test/test_model_ai_cli_workspace.py`:

```python
"""AI CLI の作業フォルダは PATH_DATA に、プロンプトの設定は PATH_APP から読む。"""

import os
import unittest
from unittest.mock import MagicMock, patch

from config import config
from model import model


class AiCliWorkspaceTests(unittest.TestCase):
    def _call(self, method_name: str, *args):
        translator = MagicMock()
        translator.checkAiCliClient.return_value = True
        with patch.object(model, "ensure_initialized"), \
                patch.object(model, "translator", translator, create=True):
            getattr(model, method_name)(*args)
        return translator.checkAiCliClient.call_args.kwargs

    def test_set_tool_passes_the_data_workspace(self) -> None:
        kwargs = self._call("setTranslatorAiCliTool", "claude")
        self.assertEqual(kwargs["root_path"], config.PATH_APP)
        self.assertEqual(kwargs["workspace"], os.path.join(config.PATH_DATA, "ai_cli_workspace"))

    def test_authentication_passes_the_data_workspace(self) -> None:
        kwargs = self._call("authenticationTranslatorAiCli")
        self.assertEqual(kwargs["root_path"], config.PATH_APP)
        self.assertEqual(kwargs["workspace"], os.path.join(config.PATH_DATA, "ai_cli_workspace"))
```

`src-python/test/test_utils_cuda_library_dirs.py` の `LOCALAPPDATA` を使うテストを、`VRCT_DATA_DIR` を使う形に直す（置き場所は `<VRCT_DATA_DIR>\cuda\bin`）。あわせて次を足す:

```python
    def test_original_vrct_folder_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as local_app_data:
            os.makedirs(os.path.join(local_app_data, "VRCT", "cuda", "bin"))
            env = {k: v for k, v in os.environ.items() if k != "VRCT_DATA_DIR"}
            env["LOCALAPPDATA"] = local_app_data
            with patch.dict(os.environ, env, clear=True):
                self.assertIsNone(utils.externalCudaLibraryDir())
```

（ファイル先頭の import に `tempfile` と `os` が無ければ足す。）

`src-python/test/test_translation_ct2_tokenizer.py` の `config.PATH_LOCAL` を `config.PATH_DATA` に変える。

- [ ] **Step 2: 失敗を確かめる**

Run: `.venv\Scripts\python -m pytest src-python/test/test_config_data_dir.py src-python/test/test_model_ai_cli_workspace.py src-python/test/test_utils_cuda_library_dirs.py -q`
Expected: FAIL（`DATA_DIR_ENV` / `dataDirectory` / `PATH_DATA` が無い、`workspace` が渡っていない）

- [ ] **Step 3: utils.py を直す**

`externalCudaLibraryDir` の前に追加し、`externalCudaLibraryDir` を置き換える:

```python
DATA_DIR_ENV = "VRCT_DATA_DIR"


def dataDirectory(app_dir: str) -> str:
    """設定・ログ・モデルなどを置くフォルダ。

    Velopack で入れた版では、本体 (VRCT-0.exe) が導入先の data\\ を環境変数
    VRCT_DATA_DIR で渡す。開発中など渡されないときは app_dir を使う。
    """
    data_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    return data_dir if data_dir else app_dir


def externalCudaLibraryDir() -> Optional[str]:
    """後から入れた CUDA ライブラリの置き場所 (存在すれば)。

    Velopack で入れた版では <導入先>\\data\\cuda\\bin (VRCT_DATA_DIR\\cuda\\bin)。
    取得処理はサブプロジェクト 2 (GPU 部品の後入れ) で作る。
    元の VRCT のフォルダ (%LOCALAPPDATA%\\VRCT) は見ない (並べて入れるため)。
    """
    data_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    if not data_dir:
        return None
    path = os.path.join(data_dir, "cuda", "bin")
    return path if os.path.isdir(path) else None
```

- [ ] **Step 4: config.py を直す**

1. `from utils import ...` に `dataDirectory` を足す。
2. ディスクリプタ `PATH_LOCAL = ManagedProperty('PATH_LOCAL', ...)` を次の 2 行に置き換える:

```python
    PATH_APP = ManagedProperty('PATH_APP', readonly=True, serialize=False)
    PATH_DATA = ManagedProperty('PATH_DATA', readonly=True, serialize=False)
```

3. `init_config` の置き場所の決め方を置き換える:

```python
        if getattr(sys, 'frozen', False):
            self._PATH_APP = os_path.dirname(sys.executable)
        else:
            self._PATH_APP = os_path.dirname(os_path.abspath(__file__))
        # 設定・ログ・モデルは PATH_DATA、同梱ファイル (_internal) は PATH_APP から読む。
        self._PATH_DATA = dataDirectory(self._PATH_APP)
        os_makedirs(self._PATH_DATA, exist_ok=True)
        self._PATH_CONFIG = os_path.join(self._PATH_DATA, "config.json")
        self._PATH_LOGS = os_path.join(self._PATH_DATA, "logs")
        os_makedirs(self._PATH_LOGS, exist_ok=True)
```

4. `loadTranslationLanguages(self.PATH_LOCAL)` を `loadTranslationLanguages(self.PATH_APP)` にする。

- [ ] **Step 5: model.py・controller.py の PATH_LOCAL を振り分ける**

`PATH_LOCAL` の使用箇所を CodeGraph と grep で全部探し、次のとおり置き換える（行番号は Task 1 のあとでずれているので、中身で探す）:

| 箇所 | 置き換え |
|---|---|
| マイク・スピーカーの `AudioTranscriber(... root=config.PATH_LOCAL ...)`（Whisper の重み） | `config.PATH_DATA` |
| `OverlayImage(config.PATH_LOCAL)`（2 か所、`_internal/fonts`） | `config.PATH_APP` |
| CTranslate2 の重みの確認・移動・取得・トークナイザー取得・`changeCTranslate2Model` | `config.PATH_DATA` |
| Whisper の重みの確認・取得 | `config.PATH_DATA` |
| LLM 系クライアント（Plamo、Gemini、OpenAI、OpenAICompatible、Groq、OpenRouter、LMStudio、Ollama）の `root_path=`（プロンプトの yml） | `config.PATH_APP` |
| `checkSudachiFullDict` / `sudachiFullDictPath` / full 辞書の取得 | `config.PATH_DATA` |
| `telemetry_state.json` | `config.PATH_DATA` |
| `controller.py` の `openFilepathConfigFile`（エクスプローラーで開くフォルダ） | `config.PATH_DATA` |

AI CLI の 2 か所（`authenticationTranslatorAiCli`、`setTranslatorAiCliTool`）は次の形にする:

```python
        return self.translator.checkAiCliClient(
            tool=config.SELECTED_AI_CLI_TOOL,  # setTranslatorAiCliTool では引数の tool
            root_path=config.PATH_APP,
            client_version=config.VERSION,
            workspace=os_path.join(config.PATH_DATA, "ai_cli_workspace"),
        )
```

`translation_translator.py` の `checkAiCliClient` に `workspace: str = None` を足し、`AICliClient(root_path=root_path, workspace=workspace, client_version=client_version)` に渡す。

`src-python/docs/` の `PATH_LOCAL` の記述を `PATH_APP` / `PATH_DATA` に直す。

- [ ] **Step 6: テストを流す**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗 0（`test_path_local_is_gone_from_the_code` も通る）

- [ ] **Step 7: Commit**

```powershell
git add -A src-python
git commit -m "refactor(paths): 同梱ファイルの場所とデータの置き場所を分け、VRCT_DATA_DIR を受け取る"
```

---

### Task 3: 本体の名前・置き場所・Velopack の組み込み（Rust）

**Files:**
- Modify: `src-tauri/Cargo.toml`（パッケージ名、`velopack`、`reqwest` / `base64` を外す、dev-dependency `tempfile`）
- Modify: `src-tauri/tauri.conf.json`
- Create: `src-tauri/src/app_paths.rs`
- Create: `src-tauri/src/uninstall.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/main.rs`
- Modify: `.gitignore`（`release/` を足す。Task 6 で使う）
- Local only（gitignore 対象、コミットしない）: `.claude/skills/run-vrct/scripts/act.ps1`・`shot.ps1` の `Get-Process VRCT` を `Get-Process VRCT-0` に、`SKILL.md` の `VRCT` プロセス名の記述を `VRCT-0` に

**Interfaces:**
- Consumes: Task 2 の `VRCT_DATA_DIR`
- Produces:
  - `vrct_lib::app_paths::{DATA_DIR_ENV, install_root(&Path) -> Option<PathBuf>, data_dir_for(&Path) -> Option<PathBuf>, prepare_data_dir(&Path) -> Option<PathBuf>, startup_log_path(&Path) -> PathBuf}`
  - `vrct_lib::uninstall::{stop_script(&Path, u32) -> String, stop_app_processes()}`
  - `pub(crate) fn startup_log(message: &str)`（lib.rs。Task 4 の updater が使う）
  - 本体の実行ファイルは `target\<profile>\VRCT-0.exe`。メインウィンドウは `tauri.conf.json` の `create: false` の設定から Rust 側で作る。

- [ ] **Step 1: Cargo.toml と tauri.conf.json を直す**

`src-tauri/Cargo.toml`:
- `[package] name = "VRCT"` を `name = "VRCT-0"` に、`description = "VRCT-0 Application"`。
- `[dependencies]` から `reqwest` と `base64` を消し、`velopack = "=1.2.158"` を足す。
- 末尾に:

```toml
[dev-dependencies]
tempfile = "3"
```

`src-tauri/tauri.conf.json`:
- `"productName": "VRCT-0"`、`"identifier": "com.lighfu.vrct0"`。
- `app.windows[0]` に `"label": "main"` と `"create": false` を足し、`"title": "VRCT-0"` にする。
- `bundle` は `"active": false` にし、`"targets"` と `"windows"`（`nsis` のブロック）を消す。`externalBin` と `resources` は残す（tauri-build が `bundle.active` に関係なく target フォルダへ写す）。`publisher` / `copyright` / `licenseFile` はそのまま。

- [ ] **Step 2: app_paths.rs を書く（テスト込み）**

`src-tauri/src/app_paths.rs`:

```rust
//! 導入先とデータの置き場所。
//!
//! Velopack で入れた版は `%LocalAppData%\VRCT-0\current\VRCT-0.exe` で動き、
//! 導入先の直下に `Update.exe` がある。そのときだけ `<導入先>\data` を
//! データの置き場所にする。開発中 (target\debug など) は今までどおり
//! 実行ファイルのフォルダを使う。

use std::path::{Path, PathBuf};

/// サイドカー (Python) にデータの置き場所を渡す環境変数。
pub const DATA_DIR_ENV: &str = "VRCT_DATA_DIR";

/// Velopack で入れた版なら導入先 (`current\` の親) を返す。
pub fn install_root(exe: &Path) -> Option<PathBuf> {
    let bin_dir = exe.parent()?;
    let is_current = bin_dir
        .file_name()
        .map(|name| name.to_string_lossy().eq_ignore_ascii_case("current"))
        .unwrap_or(false);
    if !is_current {
        return None;
    }
    let root = bin_dir.parent()?;
    root.join("Update.exe").is_file().then(|| root.to_path_buf())
}

/// Velopack で入れた版ならデータの置き場所 (`<導入先>\data`) を返す。
pub fn data_dir_for(exe: &Path) -> Option<PathBuf> {
    install_root(exe).map(|root| root.join("data"))
}

/// 起動の最初に呼ぶ。入れた版なら `data\` を作り、環境変数と作業フォルダを
/// そこへ向ける。どちらもサイドカーに引き継がれるので、サイドカーが相対パスで
/// 書くログ (process.log など) も、更新で消える `current\` ではなく `data\` に入る。
pub fn prepare_data_dir(exe: &Path) -> Option<PathBuf> {
    let data_dir = data_dir_for(exe)?;
    std::fs::create_dir_all(&data_dir).ok()?;
    std::env::set_var(DATA_DIR_ENV, &data_dir);
    // 作業フォルダを変えられなくても、環境変数だけで設定とモデルは data\ に入る。
    let _ = std::env::set_current_dir(&data_dir);
    Some(data_dir)
}

/// 起動ログの場所。入れた版は `data\logs\startup.log`、それ以外は実行ファイルの隣の `logs\`。
pub fn startup_log_path(exe: &Path) -> PathBuf {
    let base = data_dir_for(exe)
        .unwrap_or_else(|| exe.parent().unwrap_or(Path::new(".")).to_path_buf());
    base.join("logs").join("startup.log")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// `<tmp>\<sub>\current\VRCT-0.exe` と `<tmp>\<sub>\Update.exe` を作る。
    fn installed_layout(sub: &str, current: &str) -> (tempfile::TempDir, PathBuf, PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join(sub);
        fs::create_dir_all(root.join(current)).unwrap();
        fs::write(root.join("Update.exe"), b"").unwrap();
        let exe = root.join(current).join("VRCT-0.exe");
        (tmp, root, exe)
    }

    #[test]
    fn install_root_is_found_for_a_velopack_layout() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        assert_eq!(install_root(&exe), Some(root));
    }

    #[test]
    fn install_root_accepts_any_case_of_current() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "Current");
        assert_eq!(install_root(&exe), Some(root));
    }

    #[test]
    fn install_root_works_with_quotes_and_japanese_in_the_path() {
        let (_tmp, root, exe) = installed_layout("さくら's VRCT-0", "current");
        assert_eq!(install_root(&exe), Some(root.clone()));
        assert_eq!(data_dir_for(&exe), Some(root.join("data")));
    }

    #[test]
    fn dev_build_is_not_installed() {
        let tmp = tempfile::tempdir().unwrap();
        let exe = tmp.path().join("target").join("debug").join("VRCT-0.exe");
        assert_eq!(install_root(&exe), None);
        assert_eq!(data_dir_for(&exe), None);
    }

    #[test]
    fn current_folder_without_update_exe_is_not_installed() {
        let tmp = tempfile::tempdir().unwrap();
        fs::create_dir_all(tmp.path().join("current")).unwrap();
        let exe = tmp.path().join("current").join("VRCT-0.exe");
        assert_eq!(install_root(&exe), None);
    }

    #[test]
    fn startup_log_goes_to_data_when_installed() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        assert_eq!(startup_log_path(&exe), root.join("data").join("logs").join("startup.log"));
    }

    #[test]
    fn startup_log_stays_next_to_the_exe_in_dev() {
        assert_eq!(
            startup_log_path(Path::new(r"C:\VRCT\VRCT-0.exe")),
            Path::new(r"C:\VRCT\logs\startup.log")
        );
    }

    #[test]
    fn prepare_does_nothing_in_dev() {
        let tmp = tempfile::tempdir().unwrap();
        let exe = tmp.path().join("VRCT-0.exe");
        assert_eq!(prepare_data_dir(&exe), None);
        assert!(!tmp.path().join("data").exists());
    }

    #[test]
    fn prepare_sets_env_and_working_dir() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        let saved_dir = std::env::current_dir().unwrap();
        let data = prepare_data_dir(&exe).expect("installed layout");
        let env_value = std::env::var_os(DATA_DIR_ENV);
        let working_dir = std::env::current_dir().unwrap();
        std::env::set_current_dir(&saved_dir).unwrap();
        std::env::remove_var(DATA_DIR_ENV);

        assert_eq!(data, root.join("data"));
        assert!(data.is_dir());
        assert_eq!(env_value, Some(data.clone().into_os_string()));
        assert_eq!(fs::canonicalize(working_dir).unwrap(), fs::canonicalize(&data).unwrap());
    }
}
```

- [ ] **Step 3: uninstall.rs を書く（テスト込み）**

`src-tauri/src/uninstall.rs`:

```rust
//! 削除 (アンインストール) の直前に、導入先の `current\` から動いている VRCT-0 を止める。
//!
//! Velopack は削除の直前に `VRCT-0.exe` をフック用の引数で別に起動し、30 秒以内に
//! 終わることを求める。本体やサイドカーが動いたままだとファイルが使用中で消せないので、
//! ここで止める。導入先の直下にある Update.exe (削除を進めている Velopack 自身) と、
//! このフックのプロセス自身は止めない。AI CLI の子プロセスは、サイドカーが終わると
//! 標準入力の終わりを受けて自分で終了する (2026-09-24 に実機で確認済み)。

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use crate::app_paths::install_root;

/// PowerShell に渡すスクリプト。`current_dir` の下から動いているプロセスを止める。
/// パスは単一引用符で囲み、中の `'` は `''` にする。
pub fn stop_script(current_dir: &Path, self_pid: u32) -> String {
    let mut prefix = current_dir.to_string_lossy().to_string();
    if !prefix.ends_with('\\') {
        prefix.push('\\');
    }
    let quoted = prefix.replace('\'', "''");
    format!(
        "$prefix = '{quoted}'; \
         Get-CimInstance Win32_Process | \
         Where-Object {{ $_.ExecutablePath -and \
         $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and \
         $_.ProcessId -ne {self_pid} }} | \
         ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
}

/// 導入先の `current\` から動いているプロセスを止める。最長 20 秒で打ち切る。
pub fn stop_app_processes() {
    let Ok(exe) = std::env::current_exe() else { return };
    let Some(root) = install_root(&exe) else { return };
    let script = stop_script(&root.join("current"), std::process::id());

    let mut command = Command::new("powershell.exe");
    command.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", &script]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let Ok(mut child) = command.spawn() else { return };

    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
        }
    }
    let _ = child.kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_script_targets_only_the_current_folder() {
        let script = stop_script(Path::new(r"C:\Users\u\AppData\Local\VRCT-0\current"), 42);
        assert!(script.contains(r"$prefix = 'C:\Users\u\AppData\Local\VRCT-0\current\'"));
        assert!(!script.contains("Update.exe"));
    }

    #[test]
    fn stop_script_skips_its_own_process() {
        let script = stop_script(Path::new(r"C:\x\current"), 4242);
        assert!(script.contains("$_.ProcessId -ne 4242"));
    }

    #[test]
    fn stop_script_escapes_single_quotes_and_keeps_japanese() {
        let script = stop_script(Path::new(r"C:\Users\さくら's\VRCT-0\current"), 1);
        assert!(script.contains(r"'C:\Users\さくら''s\VRCT-0\current\'"));
    }

    #[test]
    fn stop_script_does_not_double_the_trailing_backslash() {
        let script = stop_script(Path::new("C:\\x\\current\\"), 1);
        assert!(script.contains(r"'C:\x\current\'"));
        assert!(!script.contains(r"current\\'"));
    }
}
```

- [ ] **Step 4: lib.rs と main.rs を直す**

`src-tauri/src/main.rs`:

```rust
// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Velopack の導入・更新・削除のフックを処理する (フックのときはここで終了する)。
    // 削除の直前には、動いている本体とサイドカーを止めてファイルを消せるようにする。
    velopack::VelopackApp::build()
        .on_before_uninstall_fast_callback(|_version| vrct_lib::uninstall::stop_app_processes())
        .run();
    vrct_lib::run()
}
```

`src-tauri/src/lib.rs`:
- 先頭に `pub mod app_paths;` と `pub mod uninstall;` を足す。
- `startup_log_path` とそのテスト（`mod tests`）を消し、`startup_log` を次にする（`pub(crate)`）:

```rust
pub(crate) fn startup_log(message: &str) {
    let Ok(executable_path) = std::env::current_exe() else {
        return;
    };
    let log_path = app_paths::startup_log_path(&executable_path);
    let Some(log_directory) = log_path.parent() else {
        return;
    };
    if create_dir_all(log_directory).is_err() {
        return;
    }
    let Ok(mut log_file) = OpenOptions::new().create(true).append(true).open(log_path) else {
        return;
    };
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_secs());
    let _ = writeln!(log_file, "[{timestamp}] {message}");
}
```

- `run()` の先頭と `setup` を次にする（メインウィンドウを設定から作り、入れた版なら WebView2 のデータを `data\webview` に置く）:

```rust
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let data_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| app_paths::prepare_data_dir(&exe));
    startup_log("VRCT-0 startup began");
    let result = tauri::Builder::default()
        .setup(move |app| {
            let window_config = app
                .config()
                .app
                .windows
                .iter()
                .find(|window| window.label == "main")
                .cloned()
                .ok_or_else(|| Error::other("main window config is missing"))?;
            let mut builder = tauri::WebviewWindowBuilder::from_config(app.handle(), &window_config)?;
            if let Some(dir) = &data_dir {
                builder = builder.data_directory(dir.join("webview"));
            }
            let main_window = builder.build()?;
            main_window.show()?;
            if let Err(error) = main_window.set_focus() {
                startup_log(&format!("Main window focus failed: {error}"));
            }
            startup_log("Main window is ready");

            #[cfg(debug_assertions)]
            { main_window.open_devtools(); }

            Ok(())
        })
```

  以降のプラグイン登録はそのまま。`invoke_handler` は `tauri::generate_handler![get_font_list]` にし、`download_zip_asset` とその `use base64...` を消す。ログの文言の `VRCT` は `VRCT-0` にする（`"VRCT-0 event loop ended"`、`"VRCT-0 startup failed: {error}"`）。

- [ ] **Step 5: テストとビルドを確かめる**

Run（PowerShell）: `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"; cargo test --manifest-path src-tauri/Cargo.toml`
Expected: `app_paths` と `uninstall` のテストがすべて PASS

Run: `npm run dev-ui` をバックグラウンドで起動し、`VRCT-0` プロセスが出てメイン画面が表示され、サイドカーが起動する（翻訳をオンにして 1 通送り、訳文が返る）ことを run-vrct スキルの手順で確かめてから止める。開発中なので `data\` は作られず、設定は今までどおり `src-tauri\target\debug\config.json` を使う。

- [ ] **Step 6: Commit**

```powershell
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/tauri.conf.json src-tauri/src .gitignore
git commit -m "feat(installer): 本体を VRCT-0 にし、Velopack で入れた版のデータを data に置く"
```

---

### Task 4: 更新役（Rust の Updater と Tauri のコマンド）

**Files:**
- Create: `src-tauri/src/updater.rs`
- Modify: `src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: Task 3 の `startup_log`
- Produces:
  - イベント `app-update://state`（`UpdateState` を JSON で送る）
  - コマンド `updater_state() -> UpdateState`、`updater_check(channel: String, manual: bool)`、`updater_download()`、`updater_restart_now() -> bool`
  - `UpdateState` の JSON（`status` で種類を表す）:
    - `{"status":"not_installed"}`、`{"status":"idle"}`、`{"status":"checking"}`、`{"status":"up_to_date"}`
    - `{"status":"available","version":"3.6.0","size_bytes":12345,"is_downgrade":false}`
    - `{"status":"downloading","version":"3.6.0","percent":42}`
    - `{"status":"ready","version":"3.6.0","restart_requested":false}`
    - `{"status":"failed","stage":"check"|"download","message":"...","version":"3.6.0"|null}`
  - 環境変数 `VRCT_UPDATE_FEED_DIR`（実機確認用。設定するとそのフォルダを更新元にする）

- [ ] **Step 1: updater.rs を書く（テスト込み）**

`src-tauri/src/updater.rs`:

```rust
//! アプリ内の更新 (Velopack)。
//!
//! 画面からは Tauri のコマンドで呼び、状態が変わるたびに `app-update://state`
//! イベントで画面へ送る。確認とダウンロードはネットワークを使うので、
//! コマンドは待たずに返し、別のスレッドで動かす。
//! 落とし終えた更新は、アプリを閉じるとき (RunEvent::Exit) に Velopack の
//! Update.exe へ渡して入れ替える。「今すぐ再起動」を選んだときだけ再起動する。
//! 閉じる前に落ちた場合は、次の起動時に VelopackApp が入れ替える (Velopack の既定)。

use std::sync::{Arc, Mutex};

use serde::Serialize;

pub const STATE_EVENT: &str = "app-update://state";
pub const REPO_URL: &str = "https://github.com/lighfu/VRCT-0";
/// 実機確認用: 設定すると、このフォルダ (vpk pack の出力) を更新元にする。
pub const FEED_DIR_ENV: &str = "VRCT_UPDATE_FEED_DIR";

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailedStage {
    Check,
    Download,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum UpdateState {
    /// Velopack で入れていない (開発中など)。
    NotInstalled,
    Idle,
    Checking,
    UpToDate,
    Available { version: String, size_bytes: u64, is_downgrade: bool },
    Downloading { version: String, percent: u8 },
    Ready { version: String, restart_requested: bool },
    Failed { stage: FailedStage, message: String, version: Option<String> },
}

#[derive(Clone, Debug, PartialEq)]
pub struct UpdateQuery {
    pub prerelease: bool,
    pub allow_downgrade: bool,
}

/// チャンネルと今の版から、何を更新の対象にするかを決める。
/// ベータ版を使っている人が安定版に切り替えたときだけ、版が下がることを許す。
pub fn update_query(channel: &str, current_version: &str) -> UpdateQuery {
    let beta = channel == "beta";
    let current_is_prerelease = current_version.contains('-');
    UpdateQuery { prerelease: beta, allow_downgrade: !beta && current_is_prerelease }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Offer {
    pub version: String,
    pub size_bytes: u64,
    pub is_downgrade: bool,
}

pub type Progress = Arc<dyn Fn(u8) + Send + Sync>;

/// Velopack との間。テストでは偽物に差し替える。
pub trait UpdateBackend: Send + Sync {
    fn is_installed(&self) -> bool;
    fn current_version(&self) -> String;
    fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String>;
    /// 直前の check が返した版を落とす。戻る前に進み具合の通知を出し終える。
    fn download(&self, progress: Progress) -> Result<(), String>;
    /// 落とした版を、このプロセスが終わったあとに入れ替えてもらう。
    fn apply_after_exit(&self, restart: bool) -> Result<(), String>;
}

type Emitter = Arc<dyn Fn(&UpdateState) + Send + Sync>;

struct Inner {
    backend: Arc<dyn UpdateBackend>,
    state: Mutex<UpdateState>,
    emitter: Mutex<Option<Emitter>>,
}

#[derive(Clone)]
pub struct Updater {
    inner: Arc<Inner>,
}

impl Updater {
    pub fn new(backend: Arc<dyn UpdateBackend>) -> Self {
        let initial = if backend.is_installed() { UpdateState::Idle } else { UpdateState::NotInstalled };
        Updater {
            inner: Arc::new(Inner { backend, state: Mutex::new(initial), emitter: Mutex::new(None) }),
        }
    }

    pub fn set_emitter(&self, emitter: Emitter) {
        *self.inner.emitter.lock().unwrap() = Some(emitter);
    }

    pub fn state(&self) -> UpdateState {
        self.inner.state.lock().unwrap().clone()
    }

    /// 今の状態から次の状態を決めて置き換え、画面へ送る。None なら何もしない。
    fn transition(&self, decide: impl FnOnce(&UpdateState) -> Option<UpdateState>) -> Option<UpdateState> {
        let next = {
            let mut state = self.inner.state.lock().unwrap();
            let next = decide(&state)?;
            *state = next.clone();
            next
        };
        let emitter = self.inner.emitter.lock().unwrap().clone();
        if let Some(emit) = emitter {
            emit(&next);
        }
        Some(next)
    }

    /// 新しい版を確かめる。manual は「更新を確認」を押したとき (失敗を表示する)。
    pub fn check(&self, channel: &str, manual: bool) {
        let started = self.transition(|state| match state {
            UpdateState::NotInstalled
            | UpdateState::Checking
            | UpdateState::Downloading { .. }
            | UpdateState::Ready { .. } => None,
            _ => Some(UpdateState::Checking),
        });
        if started.is_none() {
            return;
        }
        let query = update_query(channel, &self.inner.backend.current_version());
        let next = match self.inner.backend.check(&query) {
            Ok(Some(offer)) => UpdateState::Available {
                version: offer.version,
                size_bytes: offer.size_bytes,
                is_downgrade: offer.is_downgrade,
            },
            Ok(None) => UpdateState::UpToDate,
            Err(message) if manual => UpdateState::Failed { stage: FailedStage::Check, message, version: None },
            Err(message) => {
                crate::startup_log(&format!("Update check failed: {message}"));
                UpdateState::Idle
            }
        };
        self.transition(|_| Some(next));
    }

    /// 見つかった版を落とす。失敗したあとの再試行もここから。
    pub fn download(&self) {
        let started = self.transition(|state| match state {
            UpdateState::Available { version, .. }
            | UpdateState::Failed { stage: FailedStage::Download, version: Some(version), .. } => {
                Some(UpdateState::Downloading { version: version.clone(), percent: 0 })
            }
            _ => None,
        });
        let version = match started {
            Some(UpdateState::Downloading { version, .. }) => version,
            _ => return,
        };
        let me = self.clone();
        let progress: Progress = Arc::new(move |percent| {
            me.transition(|state| match state {
                UpdateState::Downloading { version, percent: current } if *current != percent => {
                    Some(UpdateState::Downloading { version: version.clone(), percent })
                }
                _ => None,
            });
        });
        let next = match self.inner.backend.download(progress) {
            Ok(()) => UpdateState::Ready { version, restart_requested: false },
            Err(message) => UpdateState::Failed { stage: FailedStage::Download, message, version: Some(version) },
        };
        self.transition(|_| Some(next));
    }

    /// 「今すぐ再起動して更新」。準備ができているときだけ受け付ける。
    pub fn request_restart(&self) -> bool {
        self.transition(|state| match state {
            UpdateState::Ready { version, .. } => {
                Some(UpdateState::Ready { version: version.clone(), restart_requested: true })
            }
            _ => None,
        })
        .is_some()
    }

    /// アプリを閉じるときに呼ぶ。落とし終えた版があれば入れ替えを頼む。
    pub fn apply_on_exit(&self) {
        if let UpdateState::Ready { restart_requested, .. } = self.state() {
            if let Err(message) = self.inner.backend.apply_after_exit(restart_requested) {
                crate::startup_log(&format!("Applying the update failed: {message}"));
            }
        }
    }
}

/// Velopack の UpdateManager を使う本物。
pub struct VelopackBackend {
    repo_url: String,
    pending: Mutex<Option<(velopack::UpdateManager, velopack::UpdateInfo)>>,
}

impl VelopackBackend {
    pub fn new(repo_url: &str) -> Self {
        VelopackBackend { repo_url: repo_url.to_string(), pending: Mutex::new(None) }
    }

    fn manager(&self, query: &UpdateQuery) -> Result<velopack::UpdateManager, String> {
        let options = velopack::UpdateOptions {
            AllowVersionDowngrade: query.allow_downgrade,
            ..Default::default()
        };
        let source: Box<dyn velopack::sources::UpdateSource> = match std::env::var_os(FEED_DIR_ENV) {
            Some(dir) if !dir.is_empty() => Box::new(velopack::sources::FileSource::new(dir)),
            _ => Box::new(velopack::sources::GithubSource::new(&self.repo_url, None, query.prerelease)),
        };
        velopack::UpdateManager::new_boxed(source, Some(options), None).map_err(|e| e.to_string())
    }

    fn local_manager() -> Option<velopack::UpdateManager> {
        velopack::UpdateManager::new(velopack::sources::NoneSource {}, None, None).ok()
    }
}

/// 落とす大きさ。差分があれば差分の合計、無ければ丸ごとのパッケージ。
pub fn offer_from(info: &velopack::UpdateInfo) -> Offer {
    let size_bytes = if info.BaseRelease.is_some() && !info.DeltasToTarget.is_empty() {
        info.DeltasToTarget.iter().map(|delta| delta.Size).sum()
    } else {
        info.TargetFullRelease.Size
    };
    Offer {
        version: info.TargetFullRelease.Version.clone(),
        size_bytes,
        is_downgrade: info.IsDowngrade,
    }
}

impl UpdateBackend for VelopackBackend {
    fn is_installed(&self) -> bool {
        Self::local_manager().is_some()
    }

    fn current_version(&self) -> String {
        Self::local_manager().map(|m| m.get_current_version_as_string()).unwrap_or_default()
    }

    fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String> {
        let manager = self.manager(query)?;
        match manager.check_for_updates().map_err(|e| e.to_string())? {
            velopack::UpdateCheck::UpdateAvailable(info) => {
                let offer = offer_from(&info);
                *self.pending.lock().unwrap() = Some((manager, *info));
                Ok(Some(offer))
            }
            _ => Ok(None),
        }
    }

    fn download(&self, progress: Progress) -> Result<(), String> {
        let (manager, info) = self.pending.lock().unwrap().clone().ok_or("no update to download")?;
        let (sender, receiver) = std::sync::mpsc::channel::<i16>();
        let relay = std::thread::spawn(move || {
            for percent in receiver {
                progress(percent.clamp(0, 100) as u8);
            }
        });
        let result = manager.download_updates(&info, Some(sender)).map_err(|e| e.to_string());
        let _ = relay.join();
        result
    }

    fn apply_after_exit(&self, restart: bool) -> Result<(), String> {
        let (manager, info) = self.pending.lock().unwrap().clone().ok_or("no downloaded update")?;
        manager
            .wait_exit_then_apply_updates(&info, !restart, restart, Vec::<String>::new())
            .map_err(|e| e.to_string())
    }
}

#[tauri::command]
pub fn updater_state(updater: tauri::State<'_, Updater>) -> UpdateState {
    updater.state()
}

#[tauri::command]
pub fn updater_check(updater: tauri::State<'_, Updater>, channel: String, manual: bool) {
    let updater = updater.inner().clone();
    std::thread::spawn(move || updater.check(&channel, manual));
}

#[tauri::command]
pub fn updater_download(updater: tauri::State<'_, Updater>) {
    let updater = updater.inner().clone();
    std::thread::spawn(move || updater.download());
}

#[tauri::command]
pub fn updater_restart_now(updater: tauri::State<'_, Updater>) -> bool {
    updater.request_restart()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeBackend {
        installed: bool,
        version: String,
        check_result: Mutex<Result<Option<Offer>, String>>,
        download_result: Mutex<Result<(), String>>,
        progress_steps: Vec<u8>,
        last_query: Mutex<Option<UpdateQuery>>,
        download_calls: Mutex<u32>,
        applied: Mutex<Vec<bool>>,
    }

    impl FakeBackend {
        fn new() -> Self {
            FakeBackend {
                installed: true,
                version: "3.5.1".into(),
                check_result: Mutex::new(Ok(Some(offer("3.6.0")))),
                download_result: Mutex::new(Ok(())),
                progress_steps: vec![10, 10, 50, 100],
                last_query: Mutex::new(None),
                download_calls: Mutex::new(0),
                applied: Mutex::new(Vec::new()),
            }
        }
    }

    fn offer(version: &str) -> Offer {
        Offer { version: version.into(), size_bytes: 1234, is_downgrade: false }
    }

    impl UpdateBackend for FakeBackend {
        fn is_installed(&self) -> bool {
            self.installed
        }
        fn current_version(&self) -> String {
            self.version.clone()
        }
        fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String> {
            *self.last_query.lock().unwrap() = Some(query.clone());
            self.check_result.lock().unwrap().clone()
        }
        fn download(&self, progress: Progress) -> Result<(), String> {
            *self.download_calls.lock().unwrap() += 1;
            for step in &self.progress_steps {
                progress(*step);
            }
            self.download_result.lock().unwrap().clone()
        }
        fn apply_after_exit(&self, restart: bool) -> Result<(), String> {
            self.applied.lock().unwrap().push(restart);
            Ok(())
        }
    }

    fn updater_with(backend: FakeBackend) -> (Updater, Arc<FakeBackend>, Arc<Mutex<Vec<UpdateState>>>) {
        let backend = Arc::new(backend);
        let updater = Updater::new(backend.clone());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let sink = seen.clone();
        updater.set_emitter(Arc::new(move |state| sink.lock().unwrap().push(state.clone())));
        (updater, backend, seen)
    }

    #[test]
    fn update_query_rules() {
        assert_eq!(update_query("beta", "3.5.1"), UpdateQuery { prerelease: true, allow_downgrade: false });
        assert_eq!(update_query("beta", "3.6.0-beta.1"), UpdateQuery { prerelease: true, allow_downgrade: false });
        assert_eq!(update_query("stable", "3.6.0-beta.1"), UpdateQuery { prerelease: false, allow_downgrade: true });
        assert_eq!(update_query("stable", "3.5.1"), UpdateQuery { prerelease: false, allow_downgrade: false });
    }

    #[test]
    fn not_installed_never_checks() {
        let mut fake = FakeBackend::new();
        fake.installed = false;
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", true);
        assert_eq!(updater.state(), UpdateState::NotInstalled);
        assert!(backend.last_query.lock().unwrap().is_none());
    }

    #[test]
    fn check_finds_an_update() {
        let (updater, backend, seen) = updater_with(FakeBackend::new());
        updater.check("beta", false);
        assert_eq!(
            updater.state(),
            UpdateState::Available { version: "3.6.0".into(), size_bytes: 1234, is_downgrade: false }
        );
        assert_eq!(backend.last_query.lock().unwrap().clone().unwrap().prerelease, true);
        assert_eq!(seen.lock().unwrap()[0], UpdateState::Checking);
    }

    #[test]
    fn check_reports_up_to_date() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Ok(None);
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::UpToDate);
    }

    #[test]
    fn automatic_check_failure_is_silent() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Err("403 rate limit".into());
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::Idle);
    }

    #[test]
    fn manual_check_failure_is_shown() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Err("offline".into());
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", true);
        assert_eq!(
            updater.state(),
            UpdateState::Failed { stage: FailedStage::Check, message: "offline".into(), version: None }
        );
    }

    #[test]
    fn download_reports_progress_then_ready() {
        let (updater, _, seen) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        let percents: Vec<u8> = seen
            .lock()
            .unwrap()
            .iter()
            .filter_map(|s| match s {
                UpdateState::Downloading { percent, .. } => Some(*percent),
                _ => None,
            })
            .collect();
        // 同じ値 (10) は 2 回送らない。
        assert_eq!(percents, vec![0, 10, 50, 100]);
    }

    #[test]
    fn download_failure_can_be_retried() {
        let fake = FakeBackend::new();
        *fake.download_result.lock().unwrap() = Err("connection reset".into());
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", false);
        updater.download();
        assert_eq!(
            updater.state(),
            UpdateState::Failed {
                stage: FailedStage::Download,
                message: "connection reset".into(),
                version: Some("3.6.0".into())
            }
        );
        *backend.download_result.lock().unwrap() = Ok(());
        updater.download();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        assert_eq!(*backend.download_calls.lock().unwrap(), 2);
    }

    #[test]
    fn check_is_ignored_while_downloading_or_ready() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        *backend.last_query.lock().unwrap() = None;
        updater.check("beta", true);
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        assert!(backend.last_query.lock().unwrap().is_none());
    }

    #[test]
    fn second_download_is_ignored() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        updater.download();
        assert_eq!(*backend.download_calls.lock().unwrap(), 1);
    }

    #[test]
    fn apply_on_exit_only_when_ready() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.apply_on_exit();
        assert!(backend.applied.lock().unwrap().is_empty());
        updater.download();
        updater.apply_on_exit();
        assert_eq!(*backend.applied.lock().unwrap(), vec![false]);
    }

    #[test]
    fn restart_now_is_passed_to_apply() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        assert!(!updater.request_restart());
        updater.check("stable", false);
        updater.download();
        assert!(updater.request_restart());
        updater.apply_on_exit();
        assert_eq!(*backend.applied.lock().unwrap(), vec![true]);
    }

    #[test]
    fn state_json_shape() {
        let json = serde_json::to_value(UpdateState::Downloading { version: "3.6.0".into(), percent: 5 }).unwrap();
        assert_eq!(json, serde_json::json!({"status": "downloading", "version": "3.6.0", "percent": 5}));
        let json = serde_json::to_value(UpdateState::Failed {
            stage: FailedStage::Check,
            message: "x".into(),
            version: None,
        })
        .unwrap();
        assert_eq!(json, serde_json::json!({"status": "failed", "stage": "check", "message": "x", "version": null}));
    }

    #[test]
    fn offer_size_prefers_deltas() {
        let mut info = velopack::UpdateInfo::default();
        info.TargetFullRelease.Version = "3.6.0".into();
        info.TargetFullRelease.Size = 400;
        assert_eq!(offer_from(&info).size_bytes, 400);
        info.BaseRelease = Some(velopack::VelopackAsset::default());
        let mut delta = velopack::VelopackAsset::default();
        delta.Size = 30;
        info.DeltasToTarget = vec![delta.clone(), delta];
        assert_eq!(offer_from(&info).size_bytes, 60);
    }
}
```

- [ ] **Step 2: テストを流して通ることを確かめる**

Run（PowerShell）: `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"; cargo test --manifest-path src-tauri/Cargo.toml updater`
Expected: `updater::tests` がすべて PASS（lib.rs にまだ `mod updater` が無ければ先に Step 3 の `pub mod updater;` だけ足す）

- [ ] **Step 3: lib.rs に組み込む**

- 先頭に `pub mod updater;` と `use std::sync::Arc;`、`use tauri::Emitter;` を足す。
- `run()` の中で `Builder` の前に更新役を作り、`manage` する。`setup` の最後（`Ok(())` の前）で画面への送り先を登録する:

```rust
    let updater = updater::Updater::new(Arc::new(updater::VelopackBackend::new(updater::REPO_URL)));
    let exit_updater = updater.clone();
    let result = tauri::Builder::default()
        .manage(updater)
        .setup(move |app| {
            // …(Task 3 のメインウィンドウの作成はそのまま)…
            let handle = app.handle().clone();
            app.state::<updater::Updater>().set_emitter(Arc::new(move |state| {
                let _ = handle.emit(updater::STATE_EVENT, state);
            }));
            Ok(())
        })
```

- `invoke_handler` を次にする:

```rust
        .invoke_handler(tauri::generate_handler![
            get_font_list,
            updater::updater_state,
            updater::updater_check,
            updater::updater_download,
            updater::updater_restart_now
        ])
```

- `.run(tauri::generate_context!())` を `.build(...)` と `run` に分け、閉じるときに入れ替えを頼む:

```rust
        .build(tauri::generate_context!());
    match result {
        Ok(app) => app.run(move |_handle, event| {
            if let tauri::RunEvent::Exit = event {
                exit_updater.apply_on_exit();
                startup_log("VRCT-0 event loop ended");
            }
        }),
        Err(error) => {
            startup_log(&format!("VRCT-0 startup failed: {error}"));
            panic!("error while running tauri application: {error}");
        }
    }
```

- [ ] **Step 4: テストと起動を確かめる**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`（PATH は上と同じ）
Expected: すべて PASS

Run: `npm run dev-ui` で起動し、開発者ツールのコンソールで `await window.__TAURI_INTERNALS__.invoke("updater_state")` が `{status: "not_installed"}` を返すことを確かめてから止める（開発中は Velopack で入れていないため）。

- [ ] **Step 5: Commit**

```powershell
git add src-tauri/src
git commit -m "feat(installer): アプリ内の更新役を Velopack で作り、閉じるときに入れ替える"
```

---

### Task 5: 更新の画面

**Files:**
- Create: `src-ui/logics/common/useAppUpdate.js`
- Create: `src-ui/views/app/_app_controllers/AppUpdateController.jsx`
- Modify: `src-ui/views/app/_app_controllers/index.js`、`src-ui/views/app/App.jsx`
- Modify: `src-ui/logics/store.js`、`src-ui/logics/common/index.js`、`src-ui/logics/common/useSoftwareVersion.js`、`src-ui/logics/useReceiveRoutes.js`
- Modify: `src-ui/views/app/config_page/setting_section/setting_box/updater/Updater.jsx`、`Updater.module.scss`
- Modify: `src-ui/views/app/main_page/main_section/top_bar/right_side_components/RightSideComponents.jsx`
- Modify: `src-ui/views/app/others/error_boundary/AppErrorBoundary.jsx`
- Modify: `src-ui/views/app/_app_controllers/GlobalHotKeyController.jsx`
- Delete: `src-ui/logics/common/useUpdateSoftware.js`、`useAvailableReleases.js`、`useIsSoftwareUpdating.js`、`src-ui/views/app/others/updating_component/`（`UpdatingComponent`）
- Modify: `locales/ja.yml`、`locales/en.yml`、`locales/ko.yml`、`locales/zh-Hant.yml`、`locales/zh-Hans.yml`

**Interfaces:**
- Consumes: Task 4 のコマンド・イベント・`UpdateState` の JSON。Task 1 の `/get|set/data/release_channel`（`useUpdater()` の `currentReleaseChannel` / `setReleaseChannel`）、`/get/data/version`（`useSoftwareVersion()` の `currentSoftwareVersion`）。
- Produces: `useAppUpdate()` → `{ currentAppUpdate, updateAppUpdate, syncAppUpdateState, checkAppUpdate(channel, manual), downloadAppUpdate(), restartToApplyUpdate() -> Promise<boolean> }`、atom `Atom_AppUpdate`（初期値 `{ status: "idle" }`）。

- [ ] **Step 1: store と hook を作る**

`src-ui/logics/store.js`:
- `Atom_LatestSoftwareVersionInfo`、`Atom_IsSoftwareUpdating`、`Atom_AvailableReleases` と `store.is_fetched_available_releases_already` を消す。
- 同じ「Common」の並びに追加:

```js
export const { atomInstance: Atom_AppUpdate, useHook: useStore_AppUpdate } = createAtomWithHook({ status: "idle" }, "AppUpdate", { is_state_ok: true });
```

`src-ui/logics/common/useAppUpdate.js`:

```js
import { invoke } from "@tauri-apps/api/core";
import { useStore_AppUpdate } from "@store";

// アプリ内の更新。状態は Rust の更新役 (src-tauri/src/updater.rs) が持ち、
// "app-update://state" イベントで送ってくる (AppUpdateController が受ける)。
export const useAppUpdate = () => {
    const { currentAppUpdate, updateAppUpdate } = useStore_AppUpdate();

    const syncAppUpdateState = async () => {
        updateAppUpdate(await invoke("updater_state"));
    };
    const checkAppUpdate = (channel, manual) => invoke("updater_check", { channel, manual });
    const downloadAppUpdate = () => invoke("updater_download");
    const restartToApplyUpdate = () => invoke("updater_restart_now");

    return {
        currentAppUpdate,
        updateAppUpdate,
        syncAppUpdateState,
        checkAppUpdate,
        downloadAppUpdate,
        restartToApplyUpdate,
    };
};
```

`src-ui/logics/common/index.js`: `useAvailableReleases`、`useIsSoftwareUpdating`、`useUpdateSoftware` の export を消し、`export { useAppUpdate } from "./useAppUpdate";` を足す。3 つのファイルを消す。

`src-ui/logics/common/useSoftwareVersion.js` を次にする:

```js
import { useStore_SoftwareVersion } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";

export const useSoftwareVersion = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const { currentSoftwareVersion, updateSoftwareVersion, pendingSoftwareVersion } = useStore_SoftwareVersion();

    const getSoftwareVersion = () => {
        pendingSoftwareVersion();
        asyncStdoutToPython("/get/data/version");
    };

    return {
        currentSoftwareVersion,
        getSoftwareVersion,
        updateSoftwareVersion,
    };
};
```

`src-ui/logics/useReceiveRoutes.js` の `STATIC_ROUTE_META_LIST` から `/run/update_software`、`/run/update_cuda_software`、`/get/data/available_releases`、`/run/software_update_info` の行を消す（`compute_mode`、`version`、`selectable_release_channels` は残す）。

- [ ] **Step 2: 状態を受ける controller を作る**

`src-ui/views/app/_app_controllers/AppUpdateController.jsx`:

```jsx
import { useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { useAppUpdate, useIsBackendReady } from "@logics_common";
import { useUpdater } from "@logics_configs";

// Rust の更新役から状態を受け取り、起動して設定を読み終えたら 1 回だけ新しい版を確かめる。
export const AppUpdateController = () => {
    const { updateAppUpdate, syncAppUpdateState, checkAppUpdate } = useAppUpdate();
    const { currentIsBackendReady } = useIsBackendReady();
    const { currentReleaseChannel } = useUpdater();
    const is_checked = useRef(false);

    useEffect(() => {
        let unlisten = null;
        let is_unmounted = false;
        listen("app-update://state", (event) => updateAppUpdate(event.payload)).then((fn) => {
            if (is_unmounted) fn();
            else unlisten = fn;
        });
        syncAppUpdateState().catch((error) => console.error("[AppUpdate]", error));
        return () => {
            is_unmounted = true;
            if (unlisten) unlisten();
        };
    }, []);

    useEffect(() => {
        if (is_checked.current) return;
        if (currentIsBackendReady.data !== true) return;
        if (currentReleaseChannel.state !== "ok") return;
        is_checked.current = true;
        checkAppUpdate(currentReleaseChannel.data, false).catch((error) => console.error("[AppUpdate]", error));
    }, [currentIsBackendReady.data, currentReleaseChannel.state]);

    return null;
};
```

`src-ui/views/app/_app_controllers/index.js` に `export { AppUpdateController } from "./AppUpdateController";` を足し、`App.jsx` で `<GlobalHotKeyController />` の下に `<AppUpdateController />` を置く。

`App.jsx` の `Contents` から `useIsSoftwareUpdating` と `UpdatingComponent` の分岐を消し、常に `pages_wrapper` を出す:

```jsx
const Contents = () => {
    const { WindowGeometryController } = useWindow();
    return (
        <>
            <WindowGeometryController />

            <WindowTitleBar />
            <div className={styles.pages_wrapper}>
                <ConfigPage />
                <MainPage />
                <ModalController />
            </div>
        </>
    );
};
```

（`UpdatingComponent` と `useIsSoftwareUpdating` の import も消し、`src-ui/views/app/others/updating_component/` を消す。）

`GlobalHotKeyController.jsx` から `useIsSoftwareUpdating` を外す:

```jsx
import { useEffect } from "react";
import { useHotkeys } from "@logics_configs";
import { useIsBackendReady, useIsVrctAvailable } from "@logics_common";

export const GlobalHotKeyController = () => {
    const { currentIsBackendReady } = useIsBackendReady();
    const { registerShortcuts, unregisterAll } = useHotkeys();
    const { currentIsVrctAvailable } = useIsVrctAvailable();

    useEffect(() => {
        if (currentIsVrctAvailable.data && currentIsBackendReady.data) {
            registerShortcuts();
        } else {
            unregisterAll();
        }
    }, [currentIsBackendReady.data, currentIsVrctAvailable.data]);

    return null;
};
```

- [ ] **Step 3: 更新の設定欄を作り直す**

`Updater.jsx` を丸ごと次にする:

```jsx
import { useEffect, useRef } from "react";
import { useI18n } from "@useI18n";
import styles from "./Updater.module.scss";

import { useAppUpdate, useSoftwareVersion, useWindow } from "@logics_common";
import { useUpdater } from "@logics_configs";
import { SectionLabelComponent, LabelComponent, RadioButton } from "../_components";

import CheckMarkSvg from "@images/check_mark.svg?react";
import RefreshSvg from "@images/refresh.svg?react";

const RELEASES_URL = "https://github.com/lighfu/VRCT-0/releases";

const formatSize = (bytes) => {
    if (!bytes) return "";
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
};

export const Updater = () => {
    const { t } = useI18n();
    const { currentSoftwareVersion } = useSoftwareVersion();
    const { currentReleaseChannel, setReleaseChannel } = useUpdater();
    const { currentAppUpdate, checkAppUpdate, downloadAppUpdate, restartToApplyUpdate } = useAppUpdate();
    const { asyncCloseApp } = useWindow();
    const previous_channel = useRef(currentReleaseChannel.data);

    const channel = currentReleaseChannel.data ?? "stable";
    const update = currentAppUpdate.data ?? { status: "idle" };
    const is_busy = update.status === "checking" || update.status === "downloading";

    // チャンネルを切り替えたら、そのチャンネルで確かめ直す。
    useEffect(() => {
        if (previous_channel.current === currentReleaseChannel.data) return;
        previous_channel.current = currentReleaseChannel.data;
        if (currentReleaseChannel.state === "ok") checkAppUpdate(currentReleaseChannel.data, true);
    }, [currentReleaseChannel.data, currentReleaseChannel.state]);

    const onClickCheck = () => {
        if (is_busy) return;
        checkAppUpdate(channel, true);
    };

    const onClickRestartNow = async () => {
        if (await restartToApplyUpdate()) await asyncCloseApp();
    };

    const channel_options = [
        { id: "stable", label: t("update_modal.channel_stable") },
        { id: "beta", label: t("update_modal.channel_beta") },
    ];

    return (
        <div className={styles.container}>
            <SectionLabelComponent label={t("update_modal.title")} />

            <div className={styles.summary_container}>
                <UpdateStatus
                    update={update}
                    current_version={currentSoftwareVersion.data}
                    channel={channel}
                    onClickDownload={() => downloadAppUpdate()}
                    onClickRestartNow={onClickRestartNow}
                />
            </div>

            <div className={styles.subsection_head}>
                <SectionLabelComponent label={t("update_modal.channel_label")} />
                {update.status !== "not_installed" && (
                    <button className={styles.refresh_button} onClick={onClickCheck} disabled={is_busy}>
                        <RefreshSvg className={styles.refresh_svg} />
                        <span>{t("update_modal.refresh_button")}</span>
                    </button>
                )}
            </div>

            <div className={styles.rows}>
                <div className={styles.row}>
                    <LabelComponent label={t("update_modal.channel_label")} desc={t("update_modal.channel_desc")} />
                    <RadioButton
                        name="update_modal_channel"
                        options={channel_options}
                        checked_variable={{ state: currentReleaseChannel.state, data: channel }}
                        selectFunction={(id) => setReleaseChannel(id)}
                    />
                </div>
            </div>
        </div>
    );
};

const UpdateStatus = ({ update, current_version, channel, onClickDownload, onClickRestartNow }) => {
    const { t } = useI18n();
    const channel_label = channel === "beta" ? t("update_modal.channel_beta") : t("update_modal.channel_stable");

    switch (update.status) {
        case "not_installed":
            return <p className={styles.status_text}>{t("update_modal.not_installed")}</p>;
        case "checking":
            return (
                <div className={styles.summary_loading_wrapper}>
                    <span className={styles.summary_loader} />
                    <p className={styles.status_text}>{t("update_modal.checking")}</p>
                </div>
            );
        case "available":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>
                        {t(update.is_downgrade ? "update_modal.available_downgrade" : "update_modal.available", {
                            version: update.version,
                            size: formatSize(update.size_bytes),
                        })}
                    </p>
                    <a className={styles.notes_link} href={`${RELEASES_URL}/tag/v${update.version}`} target="_blank" rel="noreferrer">
                        {t("update_modal.release_notes")}
                    </a>
                    <button className={styles.install_button} onClick={onClickDownload}>
                        {t("update_modal.download_button")}
                    </button>
                </div>
            );
        case "downloading":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>{t("update_modal.downloading", { percent: update.percent })}</p>
                    <div className={styles.progress_bar}>
                        <div className={styles.progress_fill} style={{ width: `${update.percent}%` }} />
                    </div>
                </div>
            );
        case "ready":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>{t("update_modal.ready", { version: update.version })}</p>
                    <button className={styles.install_button} onClick={onClickRestartNow} disabled={update.restart_requested}>
                        {t("update_modal.restart_now_button")}
                    </button>
                    <p className={styles.status_desc}>{t("update_modal.apply_on_exit_desc")}</p>
                </div>
            );
        case "failed":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>
                        {t(update.stage === "download" ? "update_modal.download_failed" : "update_modal.check_failed", {
                            message: update.message,
                        })}
                    </p>
                    {update.stage === "download" && (
                        <button className={styles.install_button} onClick={onClickDownload}>
                            {t("update_modal.retry_button")}
                        </button>
                    )}
                </div>
            );
        default:
            return (
                <div className={styles.up_to_date_layout}>
                    <CheckMarkSvg className={styles.up_to_date_check_svg} />
                    <div className={styles.up_to_date_text}>
                        <div className={styles.up_to_date_version}>
                            {current_version}
                            <span className={styles.up_to_date_channel}>{channel_label}</span>
                        </div>
                        {update.status === "up_to_date" && (
                            <div className={styles.up_to_date_desc}>{t("update_modal.summary_up_to_date_desc")}</div>
                        )}
                    </div>
                </div>
            );
    }
};
```

`Updater.module.scss`: 使わなくなった `diff_*` と `diff_table` / `diff_header*` の規則を消し、末尾に次を足す（色は同じファイルの既存の規則が使っている変数に合わせる）:

```scss
.status_block {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.8rem;
    width: 100%;
}

.status_text {
    font-size: 1.4rem;
}

.status_desc {
    font-size: 1.2rem;
    opacity: 0.7;
}

.notes_link {
    font-size: 1.2rem;
    text-decoration: underline;
}

.progress_bar {
    width: 100%;
    height: 0.6rem;
    border-radius: 0.3rem;
    overflow: hidden;
    background-color: var(--dark_800_color);
}

.progress_fill {
    height: 100%;
    background-color: var(--primary_400_color);
    transition: width 0.2s;
}
```

（`--dark_800_color` / `--primary_400_color` が無ければ、同じファイルの `install_button` などが使っている色変数に置き換える。）

- [ ] **Step 4: 通知ボタンとエラー画面を直す**

`RightSideComponents.jsx`: import の `useSoftwareVersion` を `useAppUpdate` に替え、`SoftwareUpdateAvailableButton` を次にする:

```jsx
const SoftwareUpdateAvailableButton = () => {
    const { t } = useI18n();
    const { currentAppUpdate } = useAppUpdate();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();

    const status = currentAppUpdate.data?.status;
    if (!["available", "downloading", "ready"].includes(status)) return null;

    return (
        <button className={styles.software_update_button} onClick={() => updateOpenedQuickSetting("update_software")}>
            <RefreshSvg className={styles.refresh_svg} />
            <p className={styles.software_update_label}>{t("main_page.update_available")}</p>
        </button>
    );
};
```

`AppErrorBoundary.jsx`: import から `useUpdateSoftware`、`useIsSoftwareUpdating`、`useSoftwareVersion`、`useComputeMode` を外して `useAppUpdate` を足し、`ActionButtons` を次にする:

```jsx
const ActionButtons = () => {
    const { currentAppUpdate, downloadAppUpdate, restartToApplyUpdate } = useAppUpdate();
    const { asyncCloseApp } = useWindow();
    const status = currentAppUpdate?.data?.status;

    const onClickUpdate = async () => {
        try {
            if (status === "available") {
                await downloadAppUpdate();
            } else if (status === "ready" && (await restartToApplyUpdate())) {
                await asyncCloseApp();
            }
        } catch (e) {
            console.error("[AppErrorBoundary] Update failed:", e);
        }
    };

    const labels = {
        available: "Update Available — Update Now",
        downloading: "Downloading update...",
        ready: "Restart to Update",
    };

    return (
        <div className={styles.action_buttons_container}>
            {labels[status] && (
                <button className={styles.update_button} onClick={onClickUpdate} disabled={status === "downloading"}>
                    {labels[status]}
                </button>
            )}
            <a className={styles.status_link_button} href={VRCT_STATUS_URL} target="_blank" rel="noreferrer">
                <span>Check VRCT Status</span>
                <ExternalLinkSvg className={styles.external_link_svg} />
            </a>
        </div>
    );
};
```

- [ ] **Step 5: 文言を直す（5 言語）**

各 `locales/*.yml` の `update_modal:` から次のキーを消す: `compute_mode_cpu`、`compute_mode_cuda`、`latest_label`、`install_latest_button`、`install_button`、`section_pick_variant`、`compute_mode_label`、`compute_mode_desc`、`version_label`、`version_desc`、`no_versions_available`、`change_col_current`、`change_col_after`、`warn_downgrade`、`warn_cuda_extra_size`、`warn_switch_channel`。`main_page.updating` と、どこからも使われていない `config_page.updater.install_panel` の塊も消す。

`update_modal:` に次のキーを足す。`ja.yml`:

```yaml
    current_version: "今の版: {{version}}"
    checking: "新しい版を確かめています…"
    available: "新しい版 {{version}} が出ています（約 {{size}}）"
    available_downgrade: "安定版の最新 {{version}} に切り替えられます（約 {{size}}）"
    release_notes: "リリースノートを見る"
    download_button: "更新"
    downloading: "ダウンロード中… {{percent}}%"
    ready: "{{version}} の準備ができました"
    restart_now_button: "今すぐ再起動して更新"
    apply_on_exit_desc: "このまま使い続けても、次にアプリを閉じたときに入れ替わります。"
    check_failed: "新しい版を確かめられませんでした: {{message}}"
    download_failed: "更新に失敗しました: {{message}}"
    retry_button: "再試行"
    not_installed: "開発版のため更新できません。"
```

`en.yml`・`ko.yml`・`zh-Hant.yml`・`zh-Hans.yml`:

```yaml
    current_version: "Current version: {{version}}"
    checking: "Checking for a new version..."
    available: "Version {{version}} is available (about {{size}})"
    available_downgrade: "You can switch to the latest stable version {{version}} (about {{size}})"
    release_notes: "Release notes"
    download_button: "Update"
    downloading: "Downloading... {{percent}}%"
    ready: "Version {{version}} is ready"
    restart_now_button: "Restart now to update"
    apply_on_exit_desc: "If you keep using the app, the update is applied the next time you close it."
    check_failed: "Could not check for a new version: {{message}}"
    download_failed: "The update failed: {{message}}"
    retry_button: "Retry"
    not_installed: "This is a development build, so it cannot be updated."
```

（`channel_desc` など残るキーの文言はそのまま。`summary_up_to_date_desc`、`refresh_button`、`channel_*`、`title` は残す。）

- [ ] **Step 6: 使われなくなった名前が残っていないか確かめる**

Run（Bash）: `grep -rn "update_cuda_software\|available_releases\|software_update_info\|IsSoftwareUpdating\|LatestSoftwareVersionInfo\|useUpdateSoftware\|UpdatingComponent\|install_panel" src-ui locales`
Expected: 何も出ない。

Run（Bash）: `grep -rn "update_software" src-ui`
Expected: `ModalController.jsx`（`case "update_software"`）と `RightSideComponents.jsx`（`updateOpenedQuickSetting("update_software")`）だけ（クイック設定の名前として残る）。

Run: `.venv\Scripts\python -c "import yaml,glob; [yaml.safe_load(open(p,encoding='utf-8')) for p in glob.glob('locales/*.yml')]; print('ok')"`
Expected: `ok`

- [ ] **Step 7: ビルドと表示を確かめる**

Run: `npm run vite-build`
Expected: 成功し、新しい警告・エラーが無い

Run: `npm run dev-ui` で起動し、設定の「アップデート」を開いて「開発版のため更新できません。」とチャンネルの選択が出ること、メイン画面に更新の通知ボタンが出ないことをスクリーンショットで確かめてから止める。

- [ ] **Step 8: Commit**

```powershell
git add -A src-ui locales
git commit -m "feat(ui): 更新の画面を Velopack の更新役に合わせて作り直す"
```

---

### Task 6: リリースの作り方

**Files:**
- Create: `utils/pack_release.py`
- Create: `src-python/test/test_pack_release.py`
- Modify: `package.json`（scripts）
- Modify: `.github/workflows/release.yml`（作り直す）
- Delete: `src-tauri/nsis/`（テンプレート・プラグイン・ライセンス）、`utils/zip.py`
- Modify: `docs/readme_build.md`（リリースの手順）、`tools/measure_footprint.py`（NSIS の setup.exe のパスを使っていれば、Velopack の `release/velopack/VRCT-0-win-Setup.exe` に）

**Interfaces:**
- Consumes: Task 3 の `target\release\VRCT-0.exe`・`VRCT-sidecar.exe`・`_internal\`
- Produces:
  - `npm run build`（`tauri build --no-bundle` まで）と `npm run release`（build のあと `utils/pack_release.py`）
  - `utils/pack_release.py`: `stage(release_dir: Path, stage_dir: Path) -> None`、`pack_args(version: str, stage_dir: Path, out_dir: Path) -> list[str]`、`main()`。出力は `release/velopack/`（`VRCT-0-win-Setup.exe`、`VRCT-0-<版>-full.nupkg`、前の版の full.nupkg があれば `-delta.nupkg`、`releases.win.json`）。

- [ ] **Step 1: テストを書く**

`src-python/test/test_pack_release.py`:

```python
"""utils/pack_release.py (Velopack のパッケージ作り) のテスト。

pytest の pythonpath は src-python で、そこにも utils.py があるため、
リポジトリ直下の utils/pack_release.py はファイルの場所から読み込む。
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pack_release", ROOT / "utils" / "pack_release.py")
pack_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack_release)


def _make_release_dir(base: Path) -> Path:
    release = base / "target_release"
    (release / "_internal" / "fonts").mkdir(parents=True)
    (release / "VRCT-0.exe").write_bytes(b"exe")
    (release / "VRCT-sidecar.exe").write_bytes(b"sidecar")
    (release / "_internal" / "fonts" / "a.ttf").write_bytes(b"font")
    (release / "VRCT-0.pdb").write_bytes(b"debug")
    (release / "build").mkdir()
    return release


class StageTests(unittest.TestCase):
    def test_stage_copies_only_the_app_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            release = _make_release_dir(base)
            stage = base / "stage"
            (stage / "old.txt").parent.mkdir(parents=True)
            (stage / "old.txt").write_text("stale")
            pack_release.stage(release, stage)
            self.assertEqual(
                sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()),
                ["VRCT-0.exe", "VRCT-sidecar.exe", "_internal/fonts/a.ttf"],
            )

    def test_stage_fails_when_a_required_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            release = _make_release_dir(base)
            (release / "VRCT-sidecar.exe").unlink()
            with self.assertRaises(SystemExit):
                pack_release.stage(release, base / "stage")


class PackArgsTests(unittest.TestCase):
    def test_pack_args(self) -> None:
        args = pack_release.pack_args("3.5.1-beta.1", Path("S"), Path("O"))
        self.assertEqual(args[0], "pack")
        pairs = dict(zip(args[1::2], args[2::2]))
        self.assertEqual(pairs["--packId"], "VRCT-0")
        self.assertEqual(pairs["--packVersion"], "3.5.1-beta.1")
        self.assertEqual(pairs["--packDir"], "S")
        self.assertEqual(pairs["--mainExe"], "VRCT-0.exe")
        self.assertEqual(pairs["--packTitle"], "VRCT-0")
        self.assertEqual(pairs["--outputDir"], "O")
        self.assertEqual(pairs["--framework"], "webview2")
        self.assertIn("--noPortable", args)
```

- [ ] **Step 2: 失敗を確かめる**

Run: `.venv\Scripts\python -m pytest src-python/test/test_pack_release.py -q`
Expected: FAIL（`utils/pack_release.py` が無い）

- [ ] **Step 3: utils/pack_release.py を書く**

```python
"""tauri build --no-bundle の出力を Velopack のパッケージにする。

npm run release から呼ぶ。src-tauri/target/release の本体・サイドカー・_internal を
release/velopack/stage に写し、vpk pack で Setup.exe と nupkg を作る。
release/velopack に前の版の full.nupkg があれば、vpk が差分 (delta) も作る
(CI では先に vpk download github で前の版を取ってくる)。
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "src-tauri" / "target" / "release"
OUT_DIR = ROOT / "release" / "velopack"
STAGE_DIR = OUT_DIR / "stage"
REQUIRED_FILES = ("VRCT-0.exe", "VRCT-sidecar.exe")
REQUIRED_DIRS = ("_internal",)


def stage(release_dir: Path, stage_dir: Path) -> None:
    """配るものだけを stage_dir に写す (前回の中身は消す)。"""
    missing = [name for name in REQUIRED_FILES if not (release_dir / name).is_file()]
    missing += [name for name in REQUIRED_DIRS if not (release_dir / name).is_dir()]
    if missing:
        sys.exit(f"pack_release: {release_dir} に無い: {', '.join(missing)}")
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    for name in REQUIRED_FILES:
        shutil.copy2(release_dir / name, stage_dir / name)
    for name in REQUIRED_DIRS:
        shutil.copytree(release_dir / name, stage_dir / name)


def pack_args(version: str, stage_dir: Path, out_dir: Path) -> list:
    """vpk pack の引数。"""
    return [
        "pack",
        "--packId", "VRCT-0",
        "--packVersion", version,
        "--packDir", str(stage_dir),
        "--mainExe", "VRCT-0.exe",
        "--packTitle", "VRCT-0",
        "--packAuthors", "lighfu",
        "--icon", str(ROOT / "src-tauri" / "icons" / "icon.ico"),
        "--splashImage", str(ROOT / "src-tauri" / "icons" / "icon.png"),
        "--framework", "webview2",
        "--outputDir", str(out_dir),
        "--noPortable",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vpk", default="vpk", help="vpk の実行ファイル")
    args = parser.parse_args()

    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    stage(RELEASE_DIR, STAGE_DIR)
    subprocess.run([args.vpk, *pack_args(version, STAGE_DIR, OUT_DIR)], check=True)
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    print(f"pack_release: {OUT_DIR} に {version} を作りました")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確かめる**

Run: `.venv\Scripts\python -m pytest src-python/test/test_pack_release.py -q`
Expected: PASS

- [ ] **Step 5: package.json と CI を直し、NSIS を消す**

`package.json` の scripts:
- `"build": "npm run task-kill && npm run clean && npm run update-version && npm run build-python && npm run vite-build && npm run tauri build -- --no-bundle"`
- `"release": "npm run build && python utils\\pack_release.py"`
- `build-cuda`、`release-cuda`、`release-all` を消す（`build-python-cuda` はサブプロジェクト 2 で使うので残す）。

`.gitignore` に `release/` があることを確かめる（Task 3 で足した）。

`.github/workflows/release.yml` を次にする（版の確認と `BUILD_CHANNEL` の確認の 2 つのステップは今のファイルからそのまま写す）:

```yaml
name: Release VRCT-0

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    name: Build and publish VRCT-0
    runs-on: windows-latest
    permissions:
      contents: write
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Get version from tag
        shell: pwsh
        run: |
          $TAG_WITH_V = $env:GITHUB_REF -replace "^refs/tags/", ""
          $VERSION_NUM = $TAG_WITH_V -replace "^v", ""
          echo "VERSION=$VERSION_NUM" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append

      - name: Determine release channel
        id: channel
        shell: pwsh
        run: |
          if ("${{ github.ref_name }}" -match "-(beta|rc)") {
            echo "is_beta=true" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
          } else {
            echo "is_beta=false" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
          }

      # (今の release.yml の 36〜84 行、「版の一致の確認」と「BUILD_CHANNEL の確認」の 2 つのステップをここにそのまま写す)

      - uses: actions/setup-node@v4
        with:
          node-version: 22.15.0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-dotnet@v4
        with:
          dotnet-version: '9.0.x'
      - name: Install vpk
        run: dotnet tool install -g vpk --version 1.2.158

      - name: Install dependencies
        run: |
          npm install
          npm run setup-python

      - name: Build
        run: npm run build

      - name: Download previous release (for delta packages)
        shell: pwsh
        run: |
          $extra = @()
          if ("${{ steps.channel.outputs.is_beta }}" -eq "true") { $extra += "--pre" }
          vpk download github --repoUrl https://github.com/lighfu/VRCT-0 --outputDir release/velopack @extra
        continue-on-error: true

      - name: Pack
        run: python utils/pack_release.py

      - name: Check asset sizes (GitHub limit 2 GiB)
        shell: pwsh
        run: |
          $limit = 2GB
          $big = Get-ChildItem release/velopack -File | Where-Object { $_.Length -ge $limit }
          if ($big) { $big | ForEach-Object { Write-Error "$($_.Name) is $($_.Length) bytes" }; exit 1 }

      - name: Publish
        shell: pwsh
        env:
          VPK_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          $extra = @()
          if ("${{ steps.channel.outputs.is_beta }}" -eq "true") { $extra += "--pre" }
          vpk upload github --repoUrl https://github.com/lighfu/VRCT-0 --outputDir release/velopack --tag "v${{ env.VERSION }}" --releaseName "VRCT-0 ${{ env.VERSION }}" --publish @extra
```

（空の文字列を native コマンドに渡すと空の引数になるので、`--pre` は配列で渡す。`vpk download github` / `vpk upload github` の `--pre` の綴りは `vpk download github --help` で確かめる。`vpk download github` は最初のリリースでは取るものが無いので `continue-on-error: true`。今の release.yml の Node・Python・Rust の準備手順と違う点があれば、今のものに合わせる。）

消す: `src-tauri/nsis/`（丸ごと）、`utils/zip.py`。`docs/readme_build.md` のリリース手順を「タグ `v<版>` を push すると CI が `npm run build` → `vpk pack` → `vpk upload github` する。手元では `npm run release` で `release/velopack/` に作る。`vpk` は `dotnet tool install -g vpk --version 1.2.158`」に書き換える。`tools/measure_footprint.py` が NSIS の setup.exe のパスを使っていれば `release/velopack/VRCT-0-win-Setup.exe` に直す。

- [ ] **Step 6: 手元で release を作って確かめる**

Run（PowerShell）: `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:USERPROFILE\.dotnet\tools;$env:Path"; dotnet tool update -g vpk --version 1.2.158; npm run release`
Expected: `release/velopack/` に `VRCT-0-win-Setup.exe`、`VRCT-0-3.5.1-beta.1-full.nupkg`、`releases.win.json` ができる。`package-lock.json` の先頭の `"version"` が変わっていたら戻す。

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗 0

- [ ] **Step 7: Commit**

```powershell
git add -A utils src-python/test/test_pack_release.py package.json .github/workflows/release.yml src-tauri/nsis docs/readme_build.md tools/measure_footprint.py .gitignore
git commit -m "build(release): NSIS をやめ、vpk で Setup と差分パッケージを作って GitHub Releases に出す"
```

---

### Task 7: 実機での確認

**Files:**
- Modify: `docs/perf/README.md`（末尾に「追記（Velopack のインストーラー）」）
- Modify: `docs/superpowers/specs/2026-09-24-installer-velopack-design.md`（確かめた結果で変わった点があれば直す）
- Local only（コミットしない）: `.claude/skills/run-vrct/SKILL.md` に「C. Velopack で入れた版」の手順を足す

**Interfaces:**
- Consumes: Task 1〜6 のすべて
- Produces: 実機での確認結果

手元の PC で、公開はしない。スクリプトはスクラッチパッドに置き、リポジトリには入れない。ユーザーの他のプロセス（claude.exe・node.exe・codex.exe など）は名前で止めない。止めるのは自分が起動した PID だけ。

- [ ] **Step 1: 事前の記録**

`%LOCALAPPDATA%\VRCT`（元の VRCT。あれば）のファイル一覧と更新時刻、`%LOCALAPPDATA%\VRCT-0` と `%LOCALAPPDATA%\com.lighfu.vrct0` と `%APPDATA%\com.lighfu.vrct0` が無いこと、`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall` の VRCT-0 の項目が無いことを記録する。

- [ ] **Step 2: 版 A を作って入れる**

`npm run release`（package.json の版 `3.5.1-beta.1`）。できた `release/velopack` を `<スクラッチ>\feed` に写す。`VRCT-0-win-Setup.exe` を実行し、ページが出ずに入って起動すること、`%LocalAppData%\VRCT-0\data\` に `config.json`・`logs\`・`process.log`・`webview\` ができること、`current\` に `process.log` などが無いこと、スタートメニューとデスクトップのショートカット、Windows の「アプリ」に VRCT-0 が出ることを確かめる。設定を 1 つ変え（例: UI の大きさ）、翻訳を 1 通送ってモデルが `data\weights` に入ることを確かめる。所要時間と Setup.exe・full.nupkg の大きさを記録する。

- [ ] **Step 3: 版 B に「今すぐ再起動」で更新する**

package.json の版を一時的に `3.5.1-beta.2` にして（コミットしない）`npm run release`。`release/velopack` に A の full.nupkg が残っているので delta もできる。できたものを `<スクラッチ>\feed` に写す。アプリを閉じ、`$env:VRCT_UPDATE_FEED_DIR = "<スクラッチ>\feed"` を設定したシェルから `%LocalAppData%\VRCT-0\VRCT-0.exe` を起動する。通知 → 「更新」→ 進み具合 → 「今すぐ再起動して更新」で B が起動し、Step 2 で変えた設定とモデルが残っていることを確かめる。落とした大きさ（delta）と所要時間を記録する。

- [ ] **Step 4: 版 C に「終了するときに更新」で更新する**

同じように `3.5.1-beta.3` を作って feed に足す。ダウンロードを終えたら「今すぐ再起動」を押さずにアプリを閉じ、もう一度起動すると C になっていることを確かめる。

- [ ] **Step 5: 入れ替えの失敗**

`3.5.1-beta.4` を作って feed に足し、ダウンロードを終えたら `%LocalAppData%\VRCT-0\packages\` の beta.4 の nupkg の途中の 1 バイトを書き換えてからアプリを閉じる。次に起動すると C のまま動き、設定が残っていること、Velopack のログ（`%LocalAppData%\VRCT-0\` の下、または `%TEMP%` の velopack のログ）に失敗が残ることを確かめる。

- [ ] **Step 6: 動いたまま削除する**

アプリを起動し、翻訳エンジンを AI CLI（claude）にして 1 通訳させ、claude のプロセスがサイドカーの子として動いていることを確かめる（開始前の PID の一覧と比べる）。そのまま Windows の「設定 → アプリ」から VRCT-0 を削除する（または `%LocalAppData%\VRCT-0\Update.exe --uninstall`）。`%LocalAppData%\VRCT-0` が丸ごと消えること、`%LOCALAPPDATA%\com.lighfu.vrct0` と `%APPDATA%\com.lighfu.vrct0` が無いこと、アンインストールの登録とショートカットが消えること、自分が起動したプロセスが残っていないこと、Step 1 で記録した元の VRCT のフォルダが変わっていないことを確かめる。

- [ ] **Step 7: 記録と後片付け**

package.json の版を `3.5.1-beta.1` に戻す（`git diff package.json` が空）。`docs/perf/README.md` の末尾に「追記（Velopack のインストーラー）」として、Setup.exe・full.nupkg・delta の大きさ、導入・更新（今すぐ／終了時）の所要時間、失敗時と削除時の結果、確かめられなかった項目を書く。spec と違う振る舞いがあれば spec を直す。`.claude/skills/run-vrct/SKILL.md` に「C. Velopack で入れた版」（`npm run release` → Setup.exe → `VRCT_UPDATE_FEED_DIR` で手元の feed から更新 → `Update.exe --uninstall`）を足す（ローカルのみ）。

```powershell
git add docs/perf/README.md docs/superpowers/specs/2026-09-24-installer-velopack-design.md
git commit -m "docs: Velopack のインストーラーの実機確認の結果を記録"
```
