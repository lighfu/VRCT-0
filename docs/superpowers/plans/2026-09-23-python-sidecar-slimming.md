# A-1: Python サイドカーのスリム化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python サイドカーから重い依存（langchain・transformers・SudachiDict-full）を外し、import を遅延させ、CPU/CUDA を単一ビルドにできる下地を作る。効果は配布サイズ・起動時間・常駐メモリの実測で示す。

**Architecture:** 先に計測ツールで基準値を取り、依存を 1 つずつ外して各段で前後比較する。置き換え部品は小さな独立モジュールにする。LLM 呼び出しの共通部は `translation_llm_common.py`、CTranslate2 用トークナイザは `translation_ct2_tokenizer.py`、Sudachi の full 辞書の取得は `transliteration_dictionary.py` に置く。既存の呼び出し側インターフェース（`XxxClient.translate()`、`Translator.translateCTranslate2()`、`downloadCTranslate2Tokenizer()`）は変えない。

**Tech Stack:** Python 3.11（`.venv`）、pytest + unittest、PyInstaller 6.10、openai SDK、google-genai、sentencepiece、SudachiPy 0.6.10、huggingface_hub、Tauri 2 / React（UI の設定宣言）、psutil（計測）。

**Spec:** `docs/superpowers/specs/2026-09-23-python-sidecar-slimming-design.md`

## Global Constraints

- コード調査は CodeGraph（`codegraph_*`）から始める。grep/Read は文字列検索か既知ファイルに限る。
- 3 指標（配布サイズ・起動時間・常駐メモリ）はどれも基準値から ±5% を超えて悪化させない。
- ユーザーに見えるエラーコード（`ErrorCode`）とメッセージは現行と同じにする。
- 後から入れる部品（full 辞書・CUDA DLL）が欠けている・壊れている場合は落ちずに標準構成（core 辞書・CPU）に戻り、`errorLogging()` を残す。
- `requirements.txt` のコメントは ASCII のみ（pip-audit の制約。ファイル内の NOTE 参照）。
- テストは unittest 形式（`unittest.TestCase`）で `src-python/test/` に置き、`.venv\Scripts\python -m pytest -q` で実行する。
- 関数名・変数名は既存コードに合わせて camelCase（例: `loadTranslatePromptConfig`）。
- 全コミットの末尾に `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` を付ける。
- 作業ブランチは `perf/slim-sidecar`。
- 順序: Task 0 → 1（計測）→ 2（§6 掃除）→ 3〜5（§2 langchain）→ 6〜7（§3 transformers）→ 8〜10（§4 Sudachi）→ 11（§5 遅延 import）→ 12（§7 単一ビルド）→ 13（最終計測）。

## Review Focus

1. **既存ユーザーの CTranslate2 トークナイザのキャッシュ**：transformers が作った HF キャッシュ（`weights/ctranslate2/<dir>/tokenizer/models--facebook--*/snapshots/<rev>/`）がそのまま使われ、ネットワークに出ないこと。→ Task 6 の `test_finds_files_in_existing_hf_cache_layout` と `test_does_not_download_when_cache_is_complete`
2. **ネットワーク無しでキャッシュも無い状態での CTranslate2 モデル切り替え**：例外がスレッドを殺さず、`is_loaded_ctranslate2_model` が False のまま残ること。→ Task 7 の `test_change_model_leaves_unloaded_when_tokenizer_fails`
3. **full 辞書のダウンロード途中で失敗・ハッシュ不一致**：壊れた `system_full.dic` が残らず、core で動き続けること。→ Task 9 の `test_hash_mismatch_leaves_no_dictionary` と Task 8 の `test_falls_back_to_core_when_full_dictionary_is_broken`
4. **LLM が空の応答（`content=None`）を返したとき**：例外にならず空文字を返し、既存の呼び出し側の空応答処理に乗ること。→ Task 3 の `test_complete_returns_empty_string_for_none_content`
5. **選んだ辞書が full なのに、ファイルを後から手で消したとき**：起動時に core に戻り、UI の `is_downloaded` が false になること。→ Task 9 の `test_selectable_dict_reports_full_missing_after_deletion`

---

### Task 0: 開発環境と基準のテスト結果

**Files:**
- Create: `docs/perf/README.md`

**Interfaces:**
- Consumes: なし
- Produces: `.venv`（CPU 版）、`.venv_cuda`（CUDA 版）、`docs/perf/README.md`（以降のタスクが追記する）

- [ ] **Step 1: 依存を入れる**

Run（リポジトリ直下、PowerShell）:
```powershell
npm install
npm run setup-python
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
Expected: `.venv` と `.venv_cuda` ができる。`setup-python` は両方を作り直すので数十分かかる。

- [ ] **Step 2: 現状のテストを流して記録する**

Run:
```powershell
.venv\Scripts\python -m pytest -q 2>&1 | Tee-Object (Join-Path $env:TEMP "vrct-pytest-baseline.txt")
```
Expected: 最終行に `N passed`（失敗があればその名前を控える）。

- [ ] **Step 3: `docs/perf/README.md` を作る**

```markdown
# 軽量化の計測記録

A-1（Python サイドカーのスリム化）の計測結果を置く。
計測方法は `tools/measure_footprint.py` の docstring を参照。

## 作業開始時のテスト結果（2026-09-23）

- pytest: <Step 2 の最終行をそのまま貼る>
- 既知の失敗: <失敗したテスト名を列挙。無ければ「なし」>
```

`<...>` は Step 2 の実際の出力で置き換える。

- [ ] **Step 4: Commit**

```powershell
git add docs/perf/README.md
git commit -m "docs(perf): 軽量化の計測記録を追加し作業開始時のテスト結果を残す"
```

---

### Task 1: 計測ツールと基準値

**Files:**
- Create: `tools/measure_footprint.py`
- Create: `src-python/test/test_measure_footprint.py`
- Create: `docs/perf/baseline-cpu-2026-09-23.json`, `docs/perf/baseline-cuda-2026-09-23.json`
- Modify: `docs/perf/README.md`

**Interfaces:**
- Consumes: Task 0 の `.venv` / `.venv_cuda`
- Produces: CLI `python tools/measure_footprint.py --label <label> --bin-dir <dir> [--installer <exe>] [--runs 5] [--python <python.exe>] [--out docs/perf]`。純関数 `summarizeDirectory(path: str, top: int = 20, exclude: frozenset[str] = EXCLUDED_ENTRIES) -> dict`、`isInitializationComplete(line: str) -> bool`、`parseImportTime(stderr_text: str, top: int = 30) -> list[dict]`。JSON のキーは `label`, `measured_at`, `size`, `installer_bytes`, `startup_seconds`, `startup_seconds_runs`, `idle_rss_bytes`, `import_time_top`, `model_loaded_rss_bytes`。

- [ ] **Step 1: 純関数の失敗するテストを書く**

`src-python/test/test_measure_footprint.py`:
```python
"""tools/measure_footprint.py の純関数のテスト。

サイドカーの起動を伴う計測本体は実機でしか意味が無いので、
ここでは集計・判定・パースだけを確かめる。
"""

import importlib.util
import json
import os
import tempfile
import unittest

_TOOL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "measure_footprint.py")
_spec = importlib.util.spec_from_file_location("measure_footprint", _TOOL_PATH)
measure_footprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure_footprint)


class SummarizeDirectoryTests(unittest.TestCase):
    def _write(self, root: str, relative: str, size: int) -> None:
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * size)

    def test_totals_and_ranks_top_level_entries(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "_internal/big/a.bin", 300)
            self._write(root, "_internal/small/b.bin", 10)
            self._write(root, "sidecar.exe", 50)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["total_bytes"], 360)
        self.assertEqual(summary["top_entries"][0], {"name": "_internal", "bytes": 310})
        self.assertEqual(summary["top_entries"][1], {"name": "sidecar.exe", "bytes": 50})

    def test_ranks_second_level_entries_under_internal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "_internal/big/a.bin", 300)
            self._write(root, "_internal/small/b.bin", 10)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["internal_top_entries"][0], {"name": "big", "bytes": 300})

    def test_excludes_runtime_generated_entries(self) -> None:
        # 初回起動でサイドカーが作る weights/ logs/ config.json は配布物ではない。
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "sidecar.exe", 50)
            self._write(root, "weights/whisper/base/model.bin", 1000)
            self._write(root, "logs/process.log", 5)
            self._write(root, "config.json", 5)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["total_bytes"], 50)


class IsInitializationCompleteTests(unittest.TestCase):
    def test_true_for_initialization_complete_response(self) -> None:
        line = json.dumps({"status": 200, "endpoint": "/run/initialization_complete", "result": {}})
        self.assertTrue(measure_footprint.isInitializationComplete(line))

    def test_false_for_other_endpoints_logs_and_garbage(self) -> None:
        self.assertFalse(measure_footprint.isInitializationComplete(
            json.dumps({"status": 200, "endpoint": "/run/initialization_progress", "result": 1})))
        self.assertFalse(measure_footprint.isInitializationComplete(
            json.dumps({"status": 348, "log": "/run/initialization_complete", "data": ""})))
        self.assertFalse(measure_footprint.isInitializationComplete("not json"))


class ParseImportTimeTests(unittest.TestCase):
    def test_returns_top_modules_by_cumulative_time(self) -> None:
        stderr_text = "\n".join([
            "import time: self [us] | cumulative | imported package",
            "import time:       100 |        100 |   small",
            "import time:      2000 |     500000 | transformers",
            "import time:        50 |      90000 |   ctranslate2",
            "unrelated line",
        ])
        top = measure_footprint.parseImportTime(stderr_text, top=2)
        self.assertEqual(top, [
            {"module": "transformers", "cumulative_us": 500000},
            {"module": "ctranslate2", "cumulative_us": 90000},
        ])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_measure_footprint.py -q`
Expected: FAIL（`tools/measure_footprint.py` が無い）

- [ ] **Step 3: 計測ツールを実装する**

`tools/measure_footprint.py`:
```python
"""VRCT サイドカーの配布サイズ・起動時間・常駐メモリを測って JSON に残す。

使い方 (リポジトリ直下):
    python tools/measure_footprint.py --label baseline-cpu --bin-dir src-tauri/bin \
        --installer src-tauri/target/release/bundle/nsis/VRCT_x.y.z_x64-setup.exe \
        --python .venv/Scripts/python.exe

- 配布サイズ: --bin-dir の合計と内訳。初回起動でサイドカーが作る
  weights/ logs/ config.json は配布物ではないので数えない。
- 起動時間: サイドカー exe を起動し、stdout に /run/initialization_complete の
  応答が出るまでの秒数。1 回目は初回ダウンロード等を含むので捨て、
  残り --runs 回の中央値を取る。サイドカーは UI からの /run/feed_watchdog を
  待つので、UI の代わりに 20 秒間隔で送る。
- 常駐メモリ: 起動完了から 30 秒後の RSS (子プロセス込み)。
- import 時間: --python で `python -X importtime -c "import mainloop"` を
  src-python を cwd にして実行し、累積時間の上位を記録する。
- モデル読み込み後の RSS: --python で Whisper small と m2m100_418M を
  読み込んだ別プロセスの RSS。重みが src-python/weights に無ければ null。
"""

import argparse
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time

EXCLUDED_ENTRIES = frozenset({"weights", "logs", "config.json"})
SIDECAR_EXE_NAME = "VRCT-sidecar-x86_64-pc-windows-msvc.exe"
FEED_WATCHDOG_LINE = json.dumps({"endpoint": "/run/feed_watchdog"}) + "\n"
_IMPORT_TIME_PATTERN = re.compile(r"^import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.+)$")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_MODEL_RSS_SCRIPT = r"""
import json, os, sys, psutil
root = sys.argv[1]
whisper_dir = os.path.join(root, "weights", "whisper", "small")
ct2_dir = os.path.join(root, "weights", "ctranslate2", "m2m100_418M-ct2-int8")
if not (os.path.isdir(whisper_dir) and os.path.isdir(ct2_dir)):
    print(json.dumps(None)); sys.exit(0)
sys.path.insert(0, root)
from faster_whisper import WhisperModel
import ctranslate2
from models.translation.translation_translator import Translator
WhisperModel(whisper_dir, device="cpu", compute_type="int8")
translator = Translator()
translator.changeCTranslate2Model(root, "m2m100_418M-ct2-int8")
print(json.dumps(psutil.Process().memory_info().rss))
"""


def _entrySize(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            total += os.path.getsize(os.path.join(dirpath, filename))
    return total


def _rankEntries(directory: str, exclude: frozenset) -> list[dict]:
    entries = [
        {"name": name, "bytes": _entrySize(os.path.join(directory, name))}
        for name in os.listdir(directory)
        if name not in exclude
    ]
    return sorted(entries, key=lambda e: e["bytes"], reverse=True)


def summarizeDirectory(path: str, top: int = 20, exclude: frozenset = EXCLUDED_ENTRIES) -> dict:
    ranked = _rankEntries(path, exclude)
    internal_dir = os.path.join(path, "_internal")
    internal_ranked = _rankEntries(internal_dir, frozenset()) if os.path.isdir(internal_dir) else []
    return {
        "total_bytes": sum(e["bytes"] for e in ranked),
        "top_entries": ranked[:top],
        "internal_top_entries": internal_ranked[:top],
    }


def isInitializationComplete(line: str) -> bool:
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("endpoint") == "/run/initialization_complete"


def parseImportTime(stderr_text: str, top: int = 30) -> list[dict]:
    modules = []
    for line in stderr_text.splitlines():
        match = _IMPORT_TIME_PATTERN.match(line)
        if match:
            modules.append({"module": match.group(3).strip(), "cumulative_us": int(match.group(2))})
    modules.sort(key=lambda m: m["cumulative_us"], reverse=True)
    return modules[:top]


def _processTreeRss(pid: int) -> int:
    import psutil
    process = psutil.Process(pid)
    total = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            pass
    return total


def _killTree(process: subprocess.Popen) -> None:
    import psutil
    try:
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass
    process.wait(timeout=30)


def _runSidecarOnce(exe: str, timeout: float, rss_wait: float | None) -> tuple[float, int | None]:
    """サイドカーを 1 回起動し、(起動秒数, RSS or None) を返して終了させる。"""
    process = subprocess.Popen(
        [exe], cwd=os.path.dirname(exe),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    started = time.perf_counter()
    completed = threading.Event()
    stop = threading.Event()

    def readStdout() -> None:
        for line in process.stdout:
            if isInitializationComplete(line.strip()):
                completed.set()

    def feedWatchdog() -> None:
        while not stop.wait(20):
            try:
                process.stdin.write(FEED_WATCHDOG_LINE)
                process.stdin.flush()
            except OSError:
                return

    threading.Thread(target=readStdout, daemon=True).start()
    threading.Thread(target=feedWatchdog, daemon=True).start()
    try:
        if not completed.wait(timeout):
            raise TimeoutError(f"initialization_complete not seen within {timeout}s")
        elapsed = time.perf_counter() - started
        rss = None
        if rss_wait is not None:
            time.sleep(rss_wait)
            rss = _processTreeRss(process.pid)
        return elapsed, rss
    finally:
        stop.set()
        _killTree(process)


def _importTimeTop(python: str) -> list[dict]:
    result = subprocess.run(
        [python, "-X", "importtime", "-c", "import mainloop"],
        cwd=os.path.join(_REPO_ROOT, "src-python"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    return parseImportTime(result.stderr)


def _modelLoadedRss(python: str) -> int | None:
    result = subprocess.run(
        [python, "-c", _MODEL_RSS_SCRIPT, os.path.join(_REPO_ROOT, "src-python")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--bin-dir", required=True)
    parser.add_argument("--installer")
    parser.add_argument("--python")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", default=os.path.join(_REPO_ROOT, "docs", "perf"))
    args = parser.parse_args()

    exe = os.path.abspath(os.path.join(args.bin_dir, SIDECAR_EXE_NAME))
    print("warm-up run (first-run downloads may take a while) ...", file=sys.stderr)
    _runSidecarOnce(exe, timeout=1800, rss_wait=None)

    runs = []
    rss = None
    for index in range(args.runs):
        elapsed, run_rss = _runSidecarOnce(exe, timeout=600, rss_wait=30 if index == 0 else None)
        runs.append(elapsed)
        rss = run_rss if run_rss is not None else rss
        print(f"run {index + 1}/{args.runs}: {elapsed:.2f}s", file=sys.stderr)

    report = {
        "label": args.label,
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "size": summarizeDirectory(args.bin_dir),
        "installer_bytes": os.path.getsize(args.installer) if args.installer else None,
        "startup_seconds": statistics.median(runs),
        "startup_seconds_runs": runs,
        "idle_rss_bytes": rss,
        "import_time_top": _importTimeTop(args.python) if args.python else None,
        "model_loaded_rss_bytes": _modelLoadedRss(args.python) if args.python else None,
    }
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{args.label}-{datetime.date.today().isoformat()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(out_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_measure_footprint.py -q`
Expected: 6 passed

- [ ] **Step 5: CPU 版の基準値を取る**

`update-version` はバージョンファイルを書き換えるので使わない。個別に回す。
```powershell
bat\build.bat
npm run vite-build
npm run tauri build
$installer = (Get-ChildItem src-tauri\target\release\bundle\nsis\*.exe | Select-Object -First 1).FullName
.venv\Scripts\python tools\measure_footprint.py --label baseline-cpu --bin-dir src-tauri\bin --installer $installer --python .venv\Scripts\python.exe
```
Expected: `docs\perf\baseline-cpu-2026-09-23.json` ができる（日付は実行日。ファイル名は出力に従う）。`startup_seconds` は数値。

- [ ] **Step 6: CUDA 版の基準値を取る**

```powershell
bat\build_cuda.bat
npm run tauri build
$installer = (Get-ChildItem src-tauri\target\release\bundle\nsis\*.exe | Select-Object -First 1).FullName
.venv_cuda\Scripts\python tools\measure_footprint.py --label baseline-cuda --bin-dir src-tauri\bin --installer $installer --python .venv_cuda\Scripts\python.exe
```
Expected: `docs\perf\baseline-cuda-<日付>.json` ができる。

- [ ] **Step 7: README に要約を追記する**

`docs/perf/README.md` の末尾に、2 つの JSON から値を写して追記する（MB は 1024² で割り小数 1 桁、秒は小数 2 桁）:
```markdown
## 基準値（作業前）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| CPU | <MB> | <MB> | <秒> | <MB> | <MB または -> |
| CUDA | <MB> | <MB> | <秒> | <MB> | <MB または -> |

import 時間の上位 5（CPU 版）: <module: ms を 5 件>
```

- [ ] **Step 8: Commit**

```powershell
git add tools/measure_footprint.py src-python/test/test_measure_footprint.py docs/perf/
git commit -m "feat(tools): サイドカーの配布サイズ・起動時間・常駐メモリの計測ツールを追加し基準値を記録"
```

---

### Task 2: 使っていない依存の掃除（§6）

**Files:**
- Create: `src-python/test/test_dependency_slimming.py`
- Modify: `package.json`, `package-lock.json`, `requirements.txt`

**Interfaces:**
- Consumes: Task 0 の `.venv`
- Produces: `test_dependency_slimming.py` のヘルパ `_requirementNames() -> set[str]`（小文字・`-` 区切りに正規化したパッケージ名）と `_importedTopLevelModules() -> set[str]`。Task 5・7・8 がこのファイルにテストを足す。

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_dependency_slimming.py`:
```python
"""軽量化 (A-1) で外した依存が戻ってこないことを確かめる。"""

import ast
import json
import os
import re
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SRC_PYTHON = os.path.join(_REPO_ROOT, "src-python")
_EXCLUDED_DIRS = {"test", "docs", "__pycache__", "weights"}


def _requirementNames() -> set[str]:
    names = set()
    with open(os.path.join(_REPO_ROOT, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-r"):
                continue
            name = re.split(r"[\s=<>@\[;]", line, maxsplit=1)[0]
            names.add(name.lower().replace("_", "-"))
    return names


def _importedTopLevelModules() -> set[str]:
    modules = set()
    for dirpath, dirnames, filenames in os.walk(_SRC_PYTHON):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules.add(node.module.split(".")[0])
    return modules


class FrontendDependencyTests(unittest.TestCase):
    def test_unused_frontend_packages_are_not_declared(self) -> None:
        with open(os.path.join(_REPO_ROOT, "package.json"), encoding="utf-8") as f:
            package = json.load(f)
        declared = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
        for name in ("@babel/standalone", "jszip", "semver"):
            with self.subTest(package=name):
                self.assertNotIn(name, declared)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_dependency_slimming.py -q`
Expected: FAIL（3 つとも `package.json` に残っている）

- [ ] **Step 3: JS の未使用依存を外す**

先に本当に使われていないことを確かめる:
```powershell
Select-String -Path src-ui\**\*.js,src-ui\**\*.jsx,locales\*.js,vite.config.js,index.html -Pattern '@babel/standalone|jszip|semver' -List
```
Expected: 何も出ない（出たら外さず、その行を報告して止まる）

```powershell
npm uninstall @babel/standalone jszip semver
npm run vite-build
```
Expected: ビルド成功

- [ ] **Step 4: Python の未使用依存を洗い出す**

直接 import されていない候補（`cloudscraper`、`exejs`、`niquests`、`aiohttp`）について、依存元と `translators` 内での使用を確かめる:
```powershell
foreach ($p in 'cloudscraper','exejs','niquests','aiohttp') { .venv\Scripts\python -m pip show $p | Select-String '^(Name|Required-by)' }
Select-String -Path .venv\Lib\site-packages\translators\*.py -Pattern 'import (cloudscraper|execjs|exejs|niquests|aiohttp)|from (cloudscraper|execjs|exejs|niquests|aiohttp)'
```
判断規則:
- `Required-by` が空で、`translators` からも import されていなければ `requirements.txt` から削除する。
- `translators` から import されていれば残し、その行の上に `# used by translators (not imported directly)` と ASCII のコメントを付ける。
- `sudachidict_core` は Task 8 で扱うのでここでは触らない。

削除したら:
```powershell
.venv\Scripts\python -m pip uninstall -y <削除したパッケージ>
.venv\Scripts\python -m pytest -q
```
Expected: Task 0 と同じ結果（新しい失敗なし）

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_dependency_slimming.py -q`
Expected: 1 passed

- [ ] **Step 6: Commit**

```powershell
git add package.json package-lock.json requirements.txt src-python/test/test_dependency_slimming.py
git commit -m "chore(deps): 使われていない JS / Python 依存を外す"
```

---

### Task 3: LLM 翻訳の共通モジュール（§2 前半）

**Files:**
- Create: `src-python/models/translation/translation_llm_common.py`
- Create: `src-python/test/test_translation_llm_common.py`

**Interfaces:**
- Consumes: なし（`openai` と `google-genai` は既にインストール済み）
- Produces:
  - `buildSystemPrompt(prompt_template: str, supported_languages: list[str], input_lang: str, output_lang: str, history_cfg: dict, context_history: list[dict]) -> str`
  - `extractText(content: Any) -> str`
  - `class OpenAIChat(base_url: str | None, api_key: str, model: str)`、メソッド `complete(messages: list[dict]) -> str`
  - `class GeminiChat(api_key: str, model: str)`、メソッド `complete(messages: list[dict]) -> str`

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_translation_llm_common.py`:
```python
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from models.translation import translation_llm_common as common

_TEMPLATE = "Translate {input_lang} to {output_lang}. Langs: {supported_languages}"
_HISTORY_CFG = {
    "use_history": True,
    "sources": ["chat", "mic"],
    "max_messages": 2,
    "max_chars": 0,
    "header_template": "History ({max_messages}):\n{history}",
    "item_template": "[{timestamp}][{source}] {text}",
}


class BuildSystemPromptTests(unittest.TestCase):
    def test_without_history_only_formats_the_template(self) -> None:
        prompt = common.buildSystemPrompt(_TEMPLATE, ["ja", "en"], "Japanese", "English", {"use_history": False}, [])
        self.assertEqual(prompt, "Translate Japanese to English. Langs: ['ja', 'en']")

    def test_history_is_filtered_by_source_and_limited_to_newest(self) -> None:
        history = [
            {"source": "chat", "text": "old", "timestamp": "2026-09-23T10:00:00"},
            {"source": "speaker", "text": "skip", "timestamp": "2026-09-23T10:01:00"},
            {"source": "mic", "text": "mid", "timestamp": "2026-09-23T10:02:00"},
            {"source": "chat", "text": "new", "timestamp": "2026-09-23T10:03:00"},
        ]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "Japanese", "English", _HISTORY_CFG, history)
        self.assertTrue(prompt.endswith("History (2):\n[10:02][mic] mid\n[10:03][chat] new"))
        self.assertNotIn("old", prompt)
        self.assertNotIn("skip", prompt)

    def test_invalid_timestamp_becomes_empty(self) -> None:
        history = [{"source": "chat", "text": "hi", "timestamp": "broken"}]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "a", "b", _HISTORY_CFG, history)
        self.assertTrue(prompt.endswith("[][chat] hi"))

    def test_history_is_truncated_from_the_front_by_max_chars(self) -> None:
        cfg = dict(_HISTORY_CFG, max_chars=5, header_template="{history}")
        history = [{"source": "chat", "text": "abcdefghij"}]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "a", "b", cfg, history)
        self.assertTrue(prompt.endswith("\n\nfghij"))


class ExtractTextTests(unittest.TestCase):
    def test_string_is_stripped(self) -> None:
        self.assertEqual(common.extractText("  hi \n"), "hi")

    def test_list_parts_are_concatenated(self) -> None:
        self.assertEqual(common.extractText(["a", {"content": "b"}, {"type": "x"}, 3]), "ab")

    def test_none_becomes_empty(self) -> None:
        self.assertEqual(common.extractText(None), "")


class OpenAIChatTests(unittest.TestCase):
    @patch("openai.OpenAI")
    def test_complete_sends_messages_and_returns_stripped_text(self, mock_openai) -> None:
        client = mock_openai.return_value
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" hello "))]
        )
        chat = common.OpenAIChat(base_url="https://example/v1", api_key="k", model="m")
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

        self.assertEqual(chat.complete(messages), "hello")
        mock_openai.assert_called_once_with(api_key="k", base_url="https://example/v1")
        client.chat.completions.create.assert_called_once_with(model="m", messages=messages, stream=False)

    @patch("openai.OpenAI")
    def test_complete_returns_empty_string_for_none_content(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
        chat = common.OpenAIChat(base_url=None, api_key="k", model="m")
        self.assertEqual(chat.complete([{"role": "user", "content": "u"}]), "")


class GeminiChatTests(unittest.TestCase):
    @patch("google.genai.Client")
    def test_complete_passes_system_prompt_as_system_instruction(self, mock_client_class) -> None:
        client = mock_client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(text=" konnichiwa ")
        chat = common.GeminiChat(api_key="k", model="gemini-x")
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]

        self.assertEqual(chat.complete(messages), "konnichiwa")
        mock_client_class.assert_called_once_with(api_key="k")
        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-x")
        self.assertEqual(kwargs["contents"], ["hello"])
        self.assertEqual(kwargs["config"].system_instruction, "sys")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_llm_common.py -q`
Expected: FAIL（`translation_llm_common` が無い）

- [ ] **Step 3: 実装する**

`src-python/models/translation/translation_llm_common.py`:
```python
"""LLM 翻訳クライアント (OpenAI / Groq / OpenRouter / LM Studio / PLaMo /
Ollama / Gemini) の共通処理。

以前は各クライアントが langchain の ChatOpenAI / ChatOllama /
ChatGoogleGenerativeAI を持ち、system プロンプトの組み立てと応答の
取り出しを 1 つずつ複製していた。使っていたのは .invoke(messages) だけ
なので、openai SDK と google-genai を直接呼ぶ形にまとめた (A-1, 2026-09-23)。
"""

from datetime import datetime
from typing import Any


def buildSystemPrompt(
    prompt_template: str,
    supported_languages: list[str],
    input_lang: str,
    output_lang: str,
    history_cfg: dict,
    context_history: list[dict],
) -> str:
    system_prompt = prompt_template.format(
        supported_languages=supported_languages,
        input_lang=input_lang,
        output_lang=output_lang,
    )
    if not history_cfg.get("use_history"):
        return system_prompt

    allowed_sources = set(history_cfg.get("sources", []))
    max_messages = int(history_cfg.get("max_messages", 0))
    max_chars = int(history_cfg.get("max_chars", 0))
    item_tmpl = history_cfg.get("item_template", "[{source}] {role}: {text}")
    header_tmpl = history_cfg.get("header_template", "{history}")

    filtered = [h for h in context_history if h.get("source") in allowed_sources]
    recent = filtered[-max_messages:] if max_messages > 0 else filtered
    formatted_items = []
    for h in recent:
        # トークン節約のため時刻は HH:MM だけにする
        timestamp_str = ""
        if "timestamp" in h:
            try:
                timestamp_str = datetime.fromisoformat(h["timestamp"]).strftime("%H:%M")
            except (TypeError, ValueError):
                timestamp_str = ""
        formatted_items.append(
            item_tmpl.format(timestamp=timestamp_str, source=h.get("source", ""), text=h.get("text", ""))
        )
    history_blob = "\n".join(formatted_items).strip()
    if max_chars and len(history_blob) > max_chars:
        history_blob = history_blob[-max_chars:]
    history_header = header_tmpl.format(max_messages=max_messages, history=history_blob)
    if history_header:
        system_prompt = f"{system_prompt}\n\n{history_header}"
    return system_prompt


def extractText(content: Any) -> str:
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text += part
            elif isinstance(part, dict) and isinstance(part.get("content"), str):
                text += part["content"]
    return text.strip()


class OpenAIChat:
    """OpenAI 互換の Chat Completions エンドポイントを 1 往復だけ呼ぶ。"""

    def __init__(self, base_url: str | None, api_key: str, model: str) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def complete(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(model=self._model, messages=messages, stream=False)
        return extractText(response.choices[0].message.content)


class GeminiChat:
    """Gemini の generate_content を 1 往復だけ呼ぶ。system メッセージは system_instruction に渡す。"""

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete(self, messages: list[dict]) -> str:
        from google.genai import types
        system_instruction = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [m["content"] for m in messages if m["role"] != "system"]
        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction or None),
        )
        return extractText(response.text)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_llm_common.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```powershell
git add src-python/models/translation/translation_llm_common.py src-python/test/test_translation_llm_common.py
git commit -m "feat(translation): LLM 翻訳の共通処理を openai SDK / google-genai 直呼びで追加"
```

---

### Task 4: OpenAI 互換の 6 クライアントを共通モジュールへ移す（§2 中盤）

**Files:**
- Modify: `src-python/models/translation/translation_openai.py`（1-3 行目の import、`updateClient` 110-116 行、`translate` 128-189 行）
- Modify: `translation_groq.py`、`translation_openrouter.py`、`translation_lmstudio.py`、`translation_plamo.py`、`translation_ollama.py`（それぞれ import・`updateClient`・`translate`）
- Create: `src-python/test/test_translation_llm_clients.py`

**Interfaces:**
- Consumes: Task 3 の `buildSystemPrompt`、`OpenAIChat`
- Produces: 各クライアントの公開メソッドは変えない（`translate(text, input_lang, output_lang) -> str`、`updateClient() -> None`）。LLM を保持する属性名も現行のまま（`openai_llm` / `groq_llm` / `openrouter_llm` / `plamo_llm`。LM Studio と Ollama は `openai_llm`）。

前提の確認（計画作成時に実施済み）: 6 本の `translate` 本体は LLM 属性名と末尾カンマ以外は一字一句同じ。実装前に念のため `translation_openai.py:128-189` と各ファイルの `translate` を見比べ、ロジックの差があれば止まって報告する。

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_translation_llm_clients.py`:
```python
"""LLM 翻訳クライアントが共通モジュール経由で呼び出すことの確認。"""

import importlib
import unittest
from unittest.mock import patch

# (モジュール名, クラス名, LLM 属性名, 期待する base_url, 初期化で渡す kwargs)
_CLIENTS = [
    ("translation_openai", "OpenAIClient", "openai_llm", None, {}),
    ("translation_groq", "GroqClient", "groq_llm", "https://api.groq.com/openai/v1", {}),
    ("translation_openrouter", "OpenRouterClient", "openrouter_llm", "https://openrouter.ai/api/v1", {}),
    ("translation_plamo", "PlamoClient", "plamo_llm", "https://api.platform.preferredai.jp/v1", {}),
    ("translation_lmstudio", "LMStudioClient", "openai_llm", "http://127.0.0.1:1234/v1", {"base_url": "http://127.0.0.1:1234/v1"}),
    ("translation_ollama", "OllamaClient", "openai_llm", "http://localhost:11434/v1", {}),
]


class OpenAICompatibleClientTests(unittest.TestCase):
    def _makeClient(self, module_name, class_name, kwargs):
        module = importlib.import_module(f"models.translation.{module_name}")
        client = getattr(module, class_name)(**kwargs)
        client.api_key = client.api_key or "key"
        client.model = "model-x"
        return module, client

    def test_update_client_builds_openai_chat_with_provider_base_url(self) -> None:
        for module_name, class_name, attr, base_url, kwargs in _CLIENTS:
            with self.subTest(client=class_name):
                module, client = self._makeClient(module_name, class_name, kwargs)
                with patch.object(module, "OpenAIChat") as mock_chat:
                    client.updateClient()
                _, call_kwargs = mock_chat.call_args
                self.assertEqual(call_kwargs["base_url"], base_url)
                self.assertEqual(call_kwargs["model"], "model-x")
                self.assertIs(getattr(client, attr), mock_chat.return_value)

    def test_translate_sends_system_and_user_messages(self) -> None:
        for module_name, class_name, attr, _, kwargs in _CLIENTS:
            with self.subTest(client=class_name):
                module, client = self._makeClient(module_name, class_name, kwargs)
                with patch.object(module, "OpenAIChat") as mock_chat:
                    mock_chat.return_value.complete.return_value = "translated"
                    client.updateClient()
                    result = client.translate("こんにちは", "Japanese", "English")
                self.assertEqual(result, "translated")
                messages = mock_chat.return_value.complete.call_args.args[0]
                self.assertEqual([m["role"] for m in messages], ["system", "user"])
                self.assertIn("English", messages[0]["content"])
                self.assertEqual(messages[1]["content"], "こんにちは")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_llm_clients.py -q`
Expected: FAIL（各モジュールに `OpenAIChat` が無い）

- [ ] **Step 3: `translation_openai.py` を書き換える**

import（1-3 行目）を次に置き換える:
```python
from openai import OpenAI

try:
    from .translation_languages import translation_lang
    from .translation_utils import loadTranslatePromptConfig
    from .translation_llm_common import OpenAIChat, buildSystemPrompt
except Exception:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from translation_languages import translation_lang, loadTranslationLanguages
    from translation_utils import loadTranslatePromptConfig
    from translation_llm_common import OpenAIChat, buildSystemPrompt
    translation_lang = loadTranslationLanguages(path=".", force=True)
```
（既存の try/except ブロックに `translation_llm_common` の 1 行ずつを足し、`from langchain_openai import ChatOpenAI` と `from pydantic import SecretStr` を消す。）

`updateClient` を次にする:
```python
    def updateClient(self) -> None:
        self.openai_llm = OpenAIChat(base_url=self.base_url, api_key=self.api_key, model=self.model)
```

`translate` を次にする:
```python
    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        system_prompt = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        return self.openai_llm.complete(messages)
```

- [ ] **Step 4: Groq・OpenRouter・PLaMo を同じ形にする**

3 ファイルそれぞれで、Step 3 と同じ 3 か所を変える。違いは LLM 属性名だけ:
- `translation_groq.py`: `self.groq_llm = OpenAIChat(base_url=self.base_url, api_key=self.api_key, model=self.model)`、`return self.groq_llm.complete(messages)`
- `translation_openrouter.py`: `self.openrouter_llm = OpenAIChat(...)`、`return self.openrouter_llm.complete(messages)`。`import requests` は残す。
- `translation_plamo.py`: `self.plamo_llm = OpenAIChat(...)`、`return self.plamo_llm.complete(messages)`

各ファイルの `from langchain_openai import ChatOpenAI` と `from pydantic import SecretStr` を消し、try/except の両側に `translation_llm_common` の import を足す。except 側の import 形はそのファイルの既存 except 側に合わせる。

- [ ] **Step 5: LM Studio と Ollama を書き換える**

`translation_lmstudio.py`: import は Step 4 と同様。`updateClient` は
```python
    def updateClient(self) -> None:
        self.openai_llm = OpenAIChat(base_url=self.base_url, api_key=self.api_key, model=self.model)
```
（`self.api_key` は既存どおり `"lmstudio"`）。`translate` は Step 3 と同じで `self.openai_llm.complete(messages)` を返す。

`translation_ollama.py`: `from langchain_ollama import ChatOllama` を消して import を足す。`updateClient` は Ollama の OpenAI 互換エンドポイントを使う:
```python
    def updateClient(self) -> None:
        # Ollama は /v1 で OpenAI 互換 API を出している。API キーは検証されないが空は拒否されるので固定値を渡す。
        self.openai_llm = OpenAIChat(base_url=f"{self.base_url}/v1", api_key="ollama", model=self.model)
```
`translate` は Step 3 と同じ。

- [ ] **Step 6: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_llm_clients.py src-python/test/test_translation_providers.py -q`
Expected: すべて passed

- [ ] **Step 7: 全テストを流す**

Run: `.venv\Scripts\python -m pytest -q`
Expected: Task 0 と同じ結果＋今回の追加分が passed

- [ ] **Step 8: Commit**

```powershell
git add src-python/models/translation/ src-python/test/test_translation_llm_clients.py
git commit -m "refactor(translation): OpenAI 互換の 6 クライアントを langchain から openai SDK 直呼びへ"
```

---

### Task 5: Gemini の移行と langchain の撤去（§2 後半）

**Files:**
- Modify: `src-python/models/translation/translation_gemini.py`（1-3 行目・16 行目、`updateClient` 98-102 行、`translate` 114 行〜）
- Modify: `requirements.txt`
- Modify: `src-python/mainloop.py:11-24`（grpcio が外れた場合のみ）
- Modify: `src-python/test/test_translation_llm_clients.py`、`src-python/test/test_dependency_slimming.py`

**Interfaces:**
- Consumes: Task 3 の `GeminiChat`、`buildSystemPrompt`。Task 2 の `_requirementNames`、`_importedTopLevelModules`
- Produces: `GeminiClient` の公開メソッドは変えない。

- [ ] **Step 1: 失敗するテストを足す**

`test_translation_llm_clients.py` に追記:
```python
class GeminiClientTests(unittest.TestCase):
    def test_translate_goes_through_gemini_chat(self) -> None:
        from models.translation import translation_gemini
        client = translation_gemini.GeminiClient()
        client.api_key = "key"
        client.model = "gemini-x"
        with patch.object(translation_gemini, "GeminiChat") as mock_chat:
            mock_chat.return_value.complete.return_value = "translated"
            client.updateClient()
            result = client.translate("こんにちは", "Japanese", "English")
        mock_chat.assert_called_once_with(api_key="key", model="gemini-x")
        self.assertEqual(result, "translated")
        messages = mock_chat.return_value.complete.call_args.args[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
```

`test_dependency_slimming.py` に追記:
```python
class LangchainRemovedTests(unittest.TestCase):
    def test_langchain_is_not_required(self) -> None:
        for name in ("langchain-openai", "langchain-google-genai", "langchain-ollama"):
            with self.subTest(package=name):
                self.assertNotIn(name, _requirementNames())

    def test_langchain_is_not_imported(self) -> None:
        imported = _importedTopLevelModules()
        for module in ("langchain_openai", "langchain_google_genai", "langchain_ollama", "langchain_core"):
            with self.subTest(module=module):
                self.assertNotIn(module, imported)

    def test_openai_sdk_is_declared_explicitly(self) -> None:
        self.assertIn("openai", _requirementNames())
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_llm_clients.py src-python/test/test_dependency_slimming.py -q`
Expected: FAIL（`GeminiChat` が無い、requirements に langchain が残っている）

- [ ] **Step 3: `translation_gemini.py` を書き換える**

- `from langchain_google_genai import ChatGoogleGenerativeAI` を消し、try/except の両側に `translation_llm_common` から `GeminiChat, buildSystemPrompt` の import を足す。
- 16 行目の `logger = logging.getLogger("langchain_google_genai")` と、その logger を使う設定行があれば消す。ほかで `logging` を使っていなければ `import logging` も消す。
- `updateClient`:
```python
    def updateClient(self) -> None:
        self.gemini_llm = GeminiChat(api_key=self.api_key, model=self.model)
```
- `translate`:
```python
    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        system_prompt = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        return self.gemini_llm.complete(messages)
```
モデル一覧と疎通確認（`genai.Client(...).models.list()`）はそのまま残す。

- [ ] **Step 4: requirements から langchain を外す**

```powershell
.venv\Scripts\python -m pip show openai | Select-String '^Version'
```
で出た版を使い、`requirements.txt` の `langchain-openai==0.3.32`、`langchain-google-genai==2.1.10`、`langchain-ollama==0.3.10` の 3 行を消して、同じ位置に次を入れる（`<版>` は上の出力）:
```
openai==<版>  # used directly by the LLM translation clients (was a langchain-openai transitive dep)
```
続けて:
```powershell
.venv\Scripts\python -m pip uninstall -y langchain-openai langchain-google-genai langchain-ollama langchain-core langsmith google-ai-generativelanguage
.venv\Scripts\python -m pip show grpcio | Select-String '^Required-by'
```
- `Required-by` が空なら、`requirements.txt` の `grpcio==1.83.1 ...` 行を消して `pip uninstall -y grpcio grpcio-status`。さらに `mainloop.py` の 11 行目 `import warnings` と 13-24 行目（grpcio の FutureWarning フィルタとコメント）を消す（`warnings` をほかで使っていなければ）。
- 空でなければ grpcio 行を残す。

`.venv_cuda` にも同じ uninstall をかける（`requirements_cuda.txt` は `-r requirements.txt` なので編集不要）。

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest -q`
Expected: Task 0 と同じ結果＋追加分が passed

- [ ] **Step 6: 実機で 1 回だけ確かめる**

API キーを持っているエンジン（OpenAI・Gemini・Groq のどれか 1 つ以上）で、`npm run dev-fast` から起動して 1 文翻訳できることを確認する。キーが無ければ、この手順を飛ばしたことをコミットメッセージの本文に書く。

- [ ] **Step 7: 計測する**

```powershell
bat\build.bat
.venv\Scripts\python tools\measure_footprint.py --label after-langchain-cpu --bin-dir src-tauri\bin --python .venv\Scripts\python.exe
```
`docs/perf/README.md` に「langchain 撤去後（CPU）」の行を Task 1 Step 7 の表の形で足す。

- [ ] **Step 8: Commit**

```powershell
git add src-python/ requirements.txt docs/perf/
git commit -m "refactor(translation): Gemini を google-genai 直呼びにして langchain 依存を撤去"
```

---

### Task 6: CTranslate2 用トークナイザ（§3 前半）

**Files:**
- Create: `src-python/models/translation/translation_ct2_tokenizer.py`
- Create: `src-python/test/test_translation_ct2_tokenizer.py`

**Interfaces:**
- Consumes: なし（`sentencepiece`、`huggingface_hub` は既存依存）
- Produces:
  - 定数 `M2M100 = "m2m100"`、`NLLB = "nllb"`、`REQUIRED_FILES: dict[str, tuple[str, ...]]`
  - `tokenizerFamily(weight_type: str) -> str`（未知の型は `ValueError`）
  - `class CT2Tokenizer(family: str, sp_model_path: str, vocab_path: str)`、メソッドは `encode(text: str, source_lang: str) -> list[str]`、`targetPrefix(target_lang: str) -> list[str]`、`decode(tokens: list[str]) -> str`
  - `findTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple[str, ...]) -> dict[str, str] | None`
  - `downloadTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple[str, ...]) -> dict[str, str]`
  - `loadCT2Tokenizer(cache_dir: str, repo_id: str, weight_type: str) -> CT2Tokenizer`

- [ ] **Step 1: 照合用に本物のトークナイザファイルを用意する（transformers がまだ入っている今のうちに）**

```powershell
cd src-python
..\.venv\Scripts\python -c "from models.translation.translation_utils import downloadCTranslate2Tokenizer as d; import config as c; d(c.config.PATH_LOCAL, 'm2m100_418M-ct2-int8'); d(c.config.PATH_LOCAL, 'nllb-200-distilled-600M-ct2-int8')"
cd ..
Get-ChildItem -Recurse src-python\weights\ctranslate2\*\tokenizer -Include sentencepiece.bpe.model,vocab.json,tokenizer.json | Select-Object FullName
```
Expected: 2 モデル分の `sentencepiece.bpe.model` と、m2m100 の `vocab.json`、NLLB の `tokenizer.json` が並ぶ（`src-python/weights` は `.gitignore` 済みか確認し、されていなければ追記する）。

- [ ] **Step 2: 失敗するテストを書く**

`src-python/test/test_translation_ct2_tokenizer.py`:
```python
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sentencepiece

from models.translation import translation_ct2_tokenizer as ct2tok

_CORPUS = [
    "hello world this is a small test corpus",
    "the quick brown fox jumps over the lazy dog",
    "translation models need tokenizers",
    "sentence piece splits words into pieces",
] * 20


def _trainSentencePiece(directory: str) -> str:
    corpus_path = os.path.join(directory, "corpus.txt")
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.write("\n".join(_CORPUS))
    prefix = os.path.join(directory, "sp")
    sentencepiece.SentencePieceTrainer.train(
        input=corpus_path, model_prefix=prefix, vocab_size=60, model_type="bpe",
        hard_vocab_limit=False, bos_id=-1, eos_id=-1, unk_id=0,
    )
    return prefix + ".model"


class CT2TokenizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.sp_path = _trainSentencePiece(cls._tmp.name)
        cls.sp = sentencepiece.SentencePieceProcessor(model_file=cls.sp_path)
        pieces = [cls.sp.id_to_piece(i) for i in range(cls.sp.get_piece_size())]
        cls.m2m_vocab_path = os.path.join(cls._tmp.name, "vocab.json")
        with open(cls.m2m_vocab_path, "w", encoding="utf-8") as f:
            json.dump({p: i for i, p in enumerate(pieces)}, f)
        cls.nllb_vocab_path = os.path.join(cls._tmp.name, "tokenizer.json")
        with open(cls.nllb_vocab_path, "w", encoding="utf-8") as f:
            json.dump({"model": {"vocab": {p: i for i, p in enumerate(pieces)}}}, f)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_family_is_derived_from_weight_type(self) -> None:
        self.assertEqual(ct2tok.tokenizerFamily("m2m100_418M-ct2-int8"), ct2tok.M2M100)
        self.assertEqual(ct2tok.tokenizerFamily("nllb-200-3.3B-ct2-int8"), ct2tok.NLLB)
        with self.assertRaises(ValueError):
            ct2tok.tokenizerFamily("unknown")

    def test_m2m100_wraps_pieces_with_language_token_and_eos(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, self.m2m_vocab_path)
        tokens = tokenizer.encode("hello world", "ja")
        self.assertEqual(tokens[0], "__ja__")
        self.assertEqual(tokens[-1], "</s>")
        self.assertEqual(tokens[1:-1], self.sp.encode("hello world", out_type=str))
        self.assertEqual(tokenizer.targetPrefix("en"), ["__en__"])

    def test_nllb_uses_raw_language_code(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.NLLB, self.sp_path, self.nllb_vocab_path)
        tokens = tokenizer.encode("hello", "jpn_Jpan")
        self.assertEqual(tokens[0], "jpn_Jpan")
        self.assertEqual(tokens[-1], "</s>")
        self.assertEqual(tokenizer.targetPrefix("eng_Latn"), ["eng_Latn"])

    def test_pieces_missing_from_vocab_become_unk(self) -> None:
        vocab_path = os.path.join(self._tmp.name, "tiny_vocab.json")
        with open(vocab_path, "w", encoding="utf-8") as f:
            json.dump({"<unk>": 0}, f)
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, vocab_path)
        tokens = tokenizer.encode("hello", "en")
        self.assertTrue(all(t == "<unk>" for t in tokens[1:-1]))

    def test_decode_drops_special_tokens_and_round_trips(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, self.m2m_vocab_path)
        pieces = self.sp.encode("the lazy dog", out_type=str)
        self.assertEqual(tokenizer.decode(pieces + ["</s>"]), "the lazy dog")


class TokenizerFilesTests(unittest.TestCase):
    def _makeSnapshot(self, cache_dir: str, repo_id: str, filenames) -> str:
        snapshot = os.path.join(cache_dir, "models--" + repo_id.replace("/", "--"), "snapshots", "abc123")
        os.makedirs(snapshot)
        for name in filenames:
            with open(os.path.join(snapshot, name), "wb") as f:
                f.write(b"x")
        return snapshot

    def test_finds_files_in_existing_hf_cache_layout(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            snapshot = self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model", "vocab.json"])
            files = ct2tok.findTokenizerFiles(cache_dir, "facebook/m2m100_418M", ct2tok.REQUIRED_FILES[ct2tok.M2M100])
        self.assertEqual(files["vocab.json"], os.path.join(snapshot, "vocab.json"))

    def test_returns_none_when_a_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model"])
            files = ct2tok.findTokenizerFiles(cache_dir, "facebook/m2m100_418M", ct2tok.REQUIRED_FILES[ct2tok.M2M100])
        self.assertIsNone(files)

    def test_does_not_download_when_cache_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model", "vocab.json"])
            with patch.object(ct2tok, "downloadTokenizerFiles") as mock_download, \
                 patch.object(ct2tok, "CT2Tokenizer") as mock_tokenizer:
                ct2tok.loadCT2Tokenizer(cache_dir, "facebook/m2m100_418M", "m2m100_418M-ct2-int8")
        mock_download.assert_not_called()
        self.assertEqual(mock_tokenizer.call_args.args[0], ct2tok.M2M100)

    def test_downloads_when_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            fake_files = {"sentencepiece.bpe.model": "a", "tokenizer.json": "b"}
            with patch.object(ct2tok, "downloadTokenizerFiles", return_value=fake_files) as mock_download, \
                 patch.object(ct2tok, "CT2Tokenizer") as mock_tokenizer:
                ct2tok.loadCT2Tokenizer(cache_dir, "facebook/nllb-200-distilled-600M", "nllb-200-distilled-600M-ct2-int8")
        mock_download.assert_called_once_with(
            cache_dir, "facebook/nllb-200-distilled-600M", ct2tok.REQUIRED_FILES[ct2tok.NLLB])
        mock_tokenizer.assert_called_once_with(ct2tok.NLLB, "a", "b")


_SENTENCES = {
    "ja": ["こんにちは、元気ですか？", "今日はいい天気ですね。", "VRChat で会いましょう。", "この翻訳は正しいですか？", "東京タワーに行きたい。"],
    "en": ["Hello, how are you?", "The weather is nice today.", "Let's meet in VRChat.", "Is this translation correct?", "I want to visit Tokyo Tower."],
    "ko": ["안녕하세요, 잘 지내세요?", "오늘 날씨가 좋네요.", "VRChat에서 만나요.", "이 번역이 맞나요?", "도쿄 타워에 가고 싶어요."],
    "zh": ["你好，你好吗？", "今天天气很好。", "我们在 VRChat 见面吧。", "这个翻译正确吗？", "我想去东京塔。"],
    "fr": ["Bonjour, comment ça va ?", "Il fait beau aujourd'hui.", "Rendez-vous sur VRChat.", "Cette traduction est-elle correcte ?", "Je veux visiter la tour de Tokyo."],
    "de": ["Hallo, wie geht es dir?", "Das Wetter ist heute schön.", "Treffen wir uns in VRChat.", "Ist diese Übersetzung richtig?", "Ich möchte den Tokyo Tower besuchen."],
}
_NLLB_CODES = {"ja": "jpn_Jpan", "en": "eng_Latn", "ko": "kor_Hang", "zh": "zho_Hans", "fr": "fra_Latn", "de": "deu_Latn"}
_PARITY_MODELS = [
    ("m2m100_418M-ct2-int8", "facebook/m2m100_418M", lambda lang: lang),
    ("nllb-200-distilled-600M-ct2-int8", "facebook/nllb-200-distilled-600M", lambda lang: _NLLB_CODES[lang]),
]


class TransformersParityTests(unittest.TestCase):
    """transformers 版と自前版で、トークン列とデコード結果が一致すること。

    transformers は requirements-dev.txt にだけ残す。トークナイザファイルが
    src-python/weights に無い環境ではスキップする (ネットワークに出ない)。
    """

    def test_tokens_and_decoding_match_transformers(self) -> None:
        try:
            import transformers
        except ImportError:
            self.skipTest("transformers is not installed")
        from config import config
        for weight_type, repo_id, to_code in _PARITY_MODELS:
            cache_dir = os.path.join(config.PATH_LOCAL, "weights", "ctranslate2", weight_type, "tokenizer")
            family = ct2tok.tokenizerFamily(weight_type)
            if ct2tok.findTokenizerFiles(cache_dir, repo_id, ct2tok.REQUIRED_FILES[family]) is None:
                self.skipTest(f"tokenizer files for {weight_type} are not cached")
            ours = ct2tok.loadCT2Tokenizer(cache_dir, repo_id, weight_type)
            theirs = transformers.AutoTokenizer.from_pretrained(repo_id, cache_dir=cache_dir, local_files_only=True)
            for lang, sentences in _SENTENCES.items():
                code = to_code(lang)
                for sentence in sentences:
                    with self.subTest(model=weight_type, lang=lang, sentence=sentence):
                        theirs.src_lang = code
                        expected = theirs.convert_ids_to_tokens(theirs.encode(sentence))
                        actual = ours.encode(sentence, code)
                        self.assertEqual(actual, expected)
                        body = actual[1:-1]
                        self.assertEqual(ours.decode(body), theirs.decode(theirs.convert_tokens_to_ids(body)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_ct2_tokenizer.py -q`
Expected: FAIL（`translation_ct2_tokenizer` が無い）

- [ ] **Step 4: 実装する**

`src-python/models/translation/translation_ct2_tokenizer.py`:
```python
"""CTranslate2 翻訳モデル (M2M100 / NLLB) 用のトークナイザ。

以前は transformers.AutoTokenizer を使っていたが、transformers は import が重く
(起動時間)、使っていたのはトークン化だけだったので sentencepiece で置き換えた
(A-1, 2026-09-23)。出力は transformers の
`convert_ids_to_tokens(encode(text))` と同じトークン文字列の列で、
test_translation_ct2_tokenizer.py の照合テストで一致を確かめている。

トークナイザファイルは transformers が作った HF キャッシュ
(<cache_dir>/models--<org>--<name>/snapshots/<rev>/) をそのまま使い、
無ければ huggingface_hub で必要なファイルだけ同じ場所へ取得する。
"""

import json
import os
from os import path as os_path

M2M100 = "m2m100"
NLLB = "nllb"

# vocab 側のファイルは「sentencepiece のピースのうち、モデルの語彙にあるもの」を
# 知るために使う。語彙に無いピースは transformers と同じく <unk> にする。
REQUIRED_FILES = {
    M2M100: ("sentencepiece.bpe.model", "vocab.json"),
    NLLB: ("sentencepiece.bpe.model", "tokenizer.json"),
}
_VOCAB_FILE = {M2M100: "vocab.json", NLLB: "tokenizer.json"}
_SPECIAL_TOKENS = frozenset({"<s>", "</s>", "<pad>"})


def tokenizerFamily(weight_type: str) -> str:
    if weight_type.startswith("m2m100"):
        return M2M100
    if weight_type.startswith("nllb"):
        return NLLB
    raise ValueError(f"unknown CTranslate2 weight type: {weight_type}")


def _loadVocab(vocab_path: str) -> frozenset:
    with open(vocab_path, encoding="utf-8") as f:
        data = json.load(f)
    vocab = data["model"]["vocab"] if "model" in data else data
    if isinstance(vocab, dict):
        return frozenset(vocab)
    return frozenset(entry[0] for entry in vocab)


class CT2Tokenizer:
    def __init__(self, family: str, sp_model_path: str, vocab_path: str) -> None:
        import sentencepiece
        self.family = family
        self._sp = sentencepiece.SentencePieceProcessor(model_file=sp_model_path)
        self._vocab = _loadVocab(vocab_path)

    def _langToken(self, lang: str) -> str:
        return f"__{lang}__" if self.family == M2M100 else lang

    def encode(self, text: str, source_lang: str) -> list[str]:
        pieces = [p if p in self._vocab else "<unk>" for p in self._sp.encode(text, out_type=str)]
        return [self._langToken(source_lang)] + pieces + ["</s>"]

    def targetPrefix(self, target_lang: str) -> list[str]:
        return [self._langToken(target_lang)]

    def decode(self, tokens: list[str]) -> str:
        return self._sp.decode_pieces([t for t in tokens if t not in _SPECIAL_TOKENS])


def findTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple) -> dict | None:
    snapshots = os_path.join(cache_dir, "models--" + repo_id.replace("/", "--"), "snapshots")
    if not os_path.isdir(snapshots):
        return None
    for revision in sorted(os.listdir(snapshots)):
        directory = os_path.join(snapshots, revision)
        if all(os_path.isfile(os_path.join(directory, name)) for name in filenames):
            return {name: os_path.join(directory, name) for name in filenames}
    return None


def downloadTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple) -> dict:
    from huggingface_hub import hf_hub_download
    return {name: hf_hub_download(repo_id=repo_id, filename=name, cache_dir=cache_dir) for name in filenames}


def loadCT2Tokenizer(cache_dir: str, repo_id: str, weight_type: str) -> CT2Tokenizer:
    family = tokenizerFamily(weight_type)
    filenames = REQUIRED_FILES[family]
    files = findTokenizerFiles(cache_dir, repo_id, filenames) or downloadTokenizerFiles(cache_dir, repo_id, filenames)
    return CT2Tokenizer(family, files["sentencepiece.bpe.model"], files[_VOCAB_FILE[family]])
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_ct2_tokenizer.py -q -rs`
Expected: 10 passed、照合テストはスキップされず passed（`-rs` にスキップ理由が出ないこと）。

照合テストが落ちた場合の扱い:
- `decode` だけ不一致で、差が先頭の空白 1 文字なら、`decode` の戻り値を `.lstrip()` するかどうかを transformers 側の出力に合わせて決める。
- `encode` が不一致なら、どのピースが違うかを出力して止まり、報告する（推測で直さない）。

- [ ] **Step 6: Commit**

```powershell
git add src-python/models/translation/translation_ct2_tokenizer.py src-python/test/test_translation_ct2_tokenizer.py .gitignore
git commit -m "feat(translation): transformers を使わない CTranslate2 用トークナイザを追加"
```

---

### Task 7: トークナイザを組み込み transformers を撤去する（§3 後半）

**Files:**
- Modify: `src-python/models/translation/translation_translator.py`（50-58 行の import、`changeCTranslate2Model` 444-476 行、`translateCTranslate2` 487-510 行）
- Modify: `src-python/models/translation/translation_utils.py`（40-43 行、`downloadCTranslate2Tokenizer` 194-206 行）
- Modify: `src-python/utils.py:61-72`
- Modify: `src-python/test/test_translation_translator_ctranslate2.py`（全面書き換え）、`src-python/test/test_translation_ctranslate2_lock.py`（`_FakeTokenizer` と transformers の差し替え部）、`src-python/test/test_dependency_slimming.py`
- Modify: `requirements.txt`、`requirements-dev.txt`

**Interfaces:**
- Consumes: Task 6 の `loadCT2Tokenizer`、`CT2Tokenizer.encode/targetPrefix/decode`
- Produces: `Translator.ctranslate2_tokenizer` は `CT2Tokenizer`（またはテストの偽物）。`translation_translator.loadCT2Tokenizer` をモジュール属性として持つ（テストが差し替える）。`downloadCTranslate2Tokenizer(path, weight_type)` の呼び出し形は変えない。

- [ ] **Step 1: 既存テストを新しいインターフェースに合わせて書き換える**

`src-python/test/test_translation_translator_ctranslate2.py` を全面的に置き換える:
```python
import unittest
from unittest.mock import MagicMock, patch

from models.translation import translation_translator as tt_module
from models.translation.translation_translator import Translator


class FakeHypothesis:
    def __init__(self, tokens: list[str]) -> None:
        self.hypotheses = [tokens]


class FakeTokenizer:
    def __init__(self, family: str) -> None:
        self.family = family

    def _lang(self, lang: str) -> str:
        return f"__{lang}__" if self.family == "m2m100" else lang

    def encode(self, text: str, source_lang: str) -> list[str]:
        return [self._lang(source_lang)] + list(text) + ["</s>"]

    def targetPrefix(self, target_lang: str) -> list[str]:
        return [self._lang(target_lang)]

    def decode(self, tokens: list[str]) -> str:
        return "".join(tokens)


class TestTranslateCTranslate2Dispatch(unittest.TestCase):
    def _translator(self, family: str) -> Translator:
        translator = Translator()
        translator.is_loaded_ctranslate2_model = True
        translator.ctranslate2_tokenizer = FakeTokenizer(family)
        translator.ctranslate2_translator = MagicMock()
        translator.ctranslate2_translator.translate_batch.return_value = [FakeHypothesis(["_prefix_", "h", "i"])]
        return translator

    def test_m2m100_uses_wrapped_language_token_as_prefix(self) -> None:
        translator = self._translator("m2m100")
        result = translator.translateCTranslate2("hi", "ja", "en", "m2m100_418M-ct2-int8")
        args, kwargs = translator.ctranslate2_translator.translate_batch.call_args
        self.assertEqual(kwargs["target_prefix"], [["__en__"]])
        self.assertEqual(args[0], [["__ja__", "h", "i", "</s>"]])
        self.assertEqual(result, "hi")

    def test_nllb_600m_uses_raw_language_code_as_prefix(self) -> None:
        translator = self._translator("nllb")
        translator.translateCTranslate2("hi", "jpn_Jpan", "eng_Latn", "nllb-200-distilled-600M-ct2-int8")
        _, kwargs = translator.ctranslate2_translator.translate_batch.call_args
        self.assertEqual(kwargs["target_prefix"], [["eng_Latn"]])

    def test_unknown_weight_type_returns_false(self) -> None:
        translator = self._translator("m2m100")
        self.assertFalse(translator.translateCTranslate2("hi", "ja", "en", "unknown-weight"))
        translator.ctranslate2_translator.translate_batch.assert_not_called()


class TestChangeCTranslate2Model(unittest.TestCase):
    def test_change_model_leaves_unloaded_when_tokenizer_fails(self) -> None:
        # オフラインでトークナイザのキャッシュも無いと loadCT2Tokenizer は例外を投げる。
        # 呼び出し元 (model.py) のスレッドを殺さず、未ロードのまま残ること。
        translator = Translator()
        fake_ct2 = MagicMock()
        with patch.object(tt_module, "ctranslate2", fake_ct2), \
             patch.object(tt_module, "loadCT2Tokenizer", side_effect=OSError("offline")):
            translator.changeCTranslate2Model(path=".", model_type="m2m100_418M-ct2-int8")
        self.assertFalse(translator.isLoadedCTranslate2Model())


if __name__ == "__main__":
    unittest.main()
```

`test_translation_ctranslate2_lock.py` を変える:
- `_FakeTokenizer`（23-58 行）を次にする。`encode` の第 2 引数で言語を受け取るので、記録するのはその値:
```python
class _FakeTokenizer:
    """CTranslate2 tokenizer の代わり。

    encode() に遅延を入れて他スレッドが割り込める窓を広げる
    (実機の tokenizer 呼び出しにも一定の処理時間がかかることを模す)。
    言語は引数で渡すので共有状態は無いが、ロックの回帰検知として残す。
    """

    def __init__(self, delay_sec: float = 0.05) -> None:
        self._delay_sec = delay_sec
        self.calls: list[tuple] = []  # (encode に渡された source_lang, message)

    def encode(self, message, source_lang):
        time.sleep(self._delay_sec)
        self.calls.append((source_lang, message))
        return ["<tok>"]

    def targetPrefix(self, target_lang):
        return [target_lang]

    def decode(self, tokens):
        return "decoded"
```
- `test_translate_and_change_model_are_mutually_exclusive` の `_FakeAutoTokenizer` / `_FakeTransformers`（123-132 行）を消し、代わりに:
```python
        def _fakeLoadCT2Tokenizer(*args, **kwargs):
            order.append("change:tokenizer_loading")
            release_change.wait(timeout=5)
            order.append("change:tokenizer_loaded")
            return _FakeTokenizer()
```
- 140 行 `original_transformers = tt_module.transformers` → `original_loader = tt_module.loadCT2Tokenizer`
- 143 行 `tt_module.transformers = _FakeTransformers()` → `tt_module.loadCT2Tokenizer = _fakeLoadCT2Tokenizer`
- 177 行 `tt_module.transformers = original_transformers` → `tt_module.loadCT2Tokenizer = original_loader`
- 117 行 `original_from_pretrained = None` は使われていなければ消す。
- 1-14 行の docstring の「tokenizer.src_lang への代入は…」の段落の後に 1 行足す: `2026-09-23: transformers を外して言語を encode() の引数で渡す形になり、src_lang の共有状態は無くなった。ロックは translator/tokenizer の差し替えとの排他のために残っている。`

`test_dependency_slimming.py` に追記:
```python
class TransformersRemovedTests(unittest.TestCase):
    def test_transformers_is_not_required_at_runtime(self) -> None:
        self.assertNotIn("transformers", _requirementNames())

    def test_transformers_is_not_imported_by_the_app(self) -> None:
        self.assertNotIn("transformers", _importedTopLevelModules())
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_translator_ctranslate2.py src-python/test/test_translation_ctranslate2_lock.py src-python/test/test_dependency_slimming.py -q`
Expected: FAIL（`translateCTranslate2` がまだ `src_lang` と `lang_code_to_token` を使っている、`loadCT2Tokenizer` 属性が無い、など）

- [ ] **Step 3: `translation_translator.py` を書き換える**

50-58 行付近の `transformers` の try/except を消し、同じ場所に既存の try/except 形式で追加する:
```python
try:
    from .translation_ct2_tokenizer import loadCT2Tokenizer
except Exception:
    from translation_ct2_tokenizer import loadCT2Tokenizer
```
（このファイルのほかの相対 import と同じ形にそろえる。）

`changeCTranslate2Model` の先頭ガードと tokenizer 読み込み部を次にする:
```python
        if ctranslate2 is None:
            return
```
```python
            try:
                self.ctranslate2_tokenizer = loadCT2Tokenizer(tokenizer_path, tokenizer, model_type)
            except Exception:
                errorLogging()
                tokenizer_path = os_path.join("./weights", "ctranslate2", directory_name, "tokenizer")
                try:
                    self.ctranslate2_tokenizer = loadCT2Tokenizer(tokenizer_path, tokenizer, model_type)
                except Exception:
                    errorLogging()
                    return
            self.is_loaded_ctranslate2_model = True
```
（2 回目の失敗で例外を外に出さず、未ロードのまま返す。`with self._ctranslate2_lock:` の内側のまま。）

`translateCTranslate2` の try 内を次にする:
```python
                try:
                    match weight_type:
                        case "m2m100_418M-ct2-int8" | "m2m100_1.2B-ct2-int8" | "nllb-200-distilled-600M-ct2-int8" | "nllb-200-distilled-1.3B-ct2-int8" | "nllb-200-3.3B-ct2-int8":
                            pass
                        case _:
                            return False
                    source = self.ctranslate2_tokenizer.encode(message, source_language)
                    target_prefix = self.ctranslate2_tokenizer.targetPrefix(target_language)
                    results = self.ctranslate2_translator.translate_batch([source], target_prefix=[target_prefix])
                    target = results[0].hypotheses[0][1:]
                    result = self.ctranslate2_tokenizer.decode(target)
                except Exception:
                    errorLogging()
```

- [ ] **Step 4: `translation_utils.py` と `utils.py` を書き換える**

`translation_utils.py` の 40-43 行（`transformers` の try/except）を消し、ファイル先頭の import 群の後に足す:
```python
try:
    from .translation_ct2_tokenizer import loadCT2Tokenizer
except Exception:
    from translation_ct2_tokenizer import loadCT2Tokenizer
```
`downloadCTranslate2Tokenizer` を次にする:
```python
def downloadCTranslate2Tokenizer(path: str, weight_type: str = "m2m100_418M-ct2-int8"):
    directory_name = ctranslate2_weights[weight_type]["directory_name"]
    tokenizer = ctranslate2_weights[weight_type]["tokenizer"]
    tokenizer_path = os_path.join(path, "weights", "ctranslate2", directory_name, "tokenizer")
    try:
        os_makedirs(tokenizer_path, exist_ok=True)
        loadCT2Tokenizer(tokenizer_path, tokenizer, weight_type)
    except Exception:
        errorLogging()
        tokenizer_path = os_path.join("./weights", "ctranslate2", directory_name, "tokenizer")
        loadCT2Tokenizer(tokenizer_path, tokenizer, weight_type)
```
docstring（46-52 行）の "and tokenizers" はそのままでよい。

`utils.py` の 61-72 行（`TRANSFORMERS_NO_ADVISORY_WARNINGS` の設定とそのコメント）を消す。

- [ ] **Step 5: requirements を直す**

- `requirements.txt` から `transformers==4.40.2` の行を消す。
- `requirements-dev.txt` の末尾に足す:
```
# Only for the tokenizer parity test (test_translation_ct2_tokenizer.py).
# The app itself uses sentencepiece directly since 2026-09-23.
transformers==4.40.2
```
- `.venv` と `.venv_cuda` の両方で:
```powershell
.venv\Scripts\python -m pip uninstall -y transformers
.venv\Scripts\python -m pip show tokenizers safetensors regex | Select-String '^(Name|Required-by)'
```
Required-by が空になったもの（faster-whisper が使う `tokenizers` は残るはず）はアンインストールする。requirements に書かれていないので requirements の編集は要らない。
- 照合テストのため `.venv` にだけ transformers を戻す: `.venv\Scripts\python -m pip install -r requirements-dev.txt`

- [ ] **Step 6: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest -q -rs`
Expected: Task 0 と同じ結果＋追加分が passed。`test_tokens_and_decoding_match_transformers` はスキップされない。

- [ ] **Step 7: 実機で確かめる**

`.venv` から transformers を一時的に抜いた状態でアプリの経路を確かめる:
```powershell
.venv\Scripts\python -m pip uninstall -y transformers
npm run dev-fast
```
翻訳エンジンを CTranslate2（m2m100_418M と nllb-600M の両方）にして日本語→英語で 1 文ずつ翻訳できることを確認する。終わったら `.venv\Scripts\python -m pip install -r requirements-dev.txt` で戻す。

- [ ] **Step 8: 計測する**

```powershell
bat\build.bat
.venv\Scripts\python tools\measure_footprint.py --label after-transformers-cpu --bin-dir src-tauri\bin --python .venv\Scripts\python.exe
```
`docs/perf/README.md` に「transformers 撤去後（CPU）」の行を足す。

- [ ] **Step 9: Commit**

```powershell
git add src-python/ requirements.txt requirements-dev.txt docs/perf/
git commit -m "refactor(translation): CTranslate2 のトークナイザを sentencepiece 実装に替えて transformers を撤去"
```

---

### Task 8: 読みがなの辞書を core に切り替える（§4 前半）

**Files:**
- Modify: `src-python/models/transliteration/transliteration_transliterator.py:1-20`
- Create: `src-python/test/test_transliteration_dictionary_selection.py`
- Modify: `requirements.txt`、`spec/backend.spec`、`spec/backend_cuda.spec`（必要な場合のみ）、`src-python/test/test_dependency_slimming.py`

**Interfaces:**
- Consumes: なし
- Produces: `Transliterator(dict_path: str | None = None)`。`dict_path` が None なら core、パスなら full（`system_full.dic`）を読む。読めなければ core に戻す。属性 `dict_type: str`（`"core"` または `"full"`）。

- [ ] **Step 1: SudachiPy が .dic のパス指定を受け付けることを確かめる**

```powershell
.venv\Scripts\python -c "from sudachipy import dictionary; import inspect; print(inspect.signature(dictionary.Dictionary))"
```
Expected: 引数に `dict` がある。無ければ止まって報告する（以降の `dictionary.Dictionary(dict=...)` を書き換える必要がある）。

- [ ] **Step 2: 失敗するテストを書く**

`src-python/test/test_transliteration_dictionary_selection.py`:
```python
import os
import tempfile
import unittest

from models.transliteration.transliteration_transliterator import Transliterator


class DictionarySelectionTests(unittest.TestCase):
    def test_uses_core_dictionary_by_default(self) -> None:
        transliterator = Transliterator()
        self.assertEqual(transliterator.dict_type, "core")
        result = transliterator.analyze("東京に行きます", use_macron=False)
        readings = "".join(part.get("hira", "") for part in result)
        self.assertIn("とうきょう", readings)

    def test_falls_back_to_core_when_full_dictionary_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = os.path.join(directory, "system_full.dic")
            with open(broken, "wb") as f:
                f.write(b"not a sudachi dictionary")
            transliterator = Transliterator(dict_path=broken)
        self.assertEqual(transliterator.dict_type, "core")
        self.assertTrue(transliterator.analyze("東京", use_macron=False))

    def test_falls_back_to_core_when_full_dictionary_is_missing(self) -> None:
        transliterator = Transliterator(dict_path=os.path.join("no", "such", "system_full.dic"))
        self.assertEqual(transliterator.dict_type, "core")
```

`test_dependency_slimming.py` に追記:
```python
class SudachiDictionaryTests(unittest.TestCase):
    def test_only_the_core_dictionary_is_bundled(self) -> None:
        names = _requirementNames()
        self.assertIn("sudachidict-core", names)
        self.assertNotIn("sudachidict-full", names)
```

`analyze()` の戻り値の形（`hira` キーの有無）は `transliteration_transliterator.py` の `analyze` の docstring で確かめ、違っていればテストのキー名を実物に合わせる。

- [ ] **Step 3: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_transliteration_dictionary_selection.py src-python/test/test_dependency_slimming.py -q`
Expected: FAIL（`dict_type` 属性が無い、`dict_path` 引数が無い、requirements に full がある）

- [ ] **Step 4: 実装する**

`transliteration_transliterator.py` の import に足す（既存の try/except 形式にそろえる）:
```python
from typing import Optional
try:
    from utils import errorLogging
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from utils import errorLogging
```
`__init__` を次にする:
```python
class Transliterator:
    def __init__(self, dict_path: Optional[str] = None) -> None:
        # 標準は同梱の core 辞書。設定で full を選び、ダウンロード済みなら
        # そのファイル (system_full.dic) を読む。読めなければ core に戻す。
        self.dict_type = "core"
        self.tokenizer_obj = None
        if dict_path is not None:
            try:
                self.tokenizer_obj = dictionary.Dictionary(dict=dict_path).create()
                self.dict_type = "full"
            except Exception:
                errorLogging()
        if self.tokenizer_obj is None:
            self.tokenizer_obj = dictionary.Dictionary(dict="core").create()
        self.mode = tokenizer.Tokenizer.SplitMode.C
        # Lock to prevent concurrent access to sudachipy tokenizer which may
        # internally use Rust/PyO3 borrow semantics and raise "Already borrowed".
        self._tokenizer_lock = threading.Lock()
```

- [ ] **Step 5: requirements と PyInstaller の設定を直す**

- `requirements.txt` から `SudachiDict-full==20250825` を消す。`.venv` と `.venv_cuda` で `pip uninstall -y SudachiDict-full`。
- `bat\build.bat` を実行し、`src-tauri\bin\_internal` に `sudachidict_core` があり `sudachidict_full` が無いことを確かめる:
```powershell
Get-ChildItem src-tauri\bin\_internal -Directory -Filter sudachidict* | Select-Object Name
```
Expected: `sudachidict_core` だけ。`sudachidict_core` が無ければ、`spec/backend.spec` と `spec/backend_cuda.spec` の `datas` に `('./../.venv/Lib/site-packages/sudachidict_core', 'sudachidict_core/')` を足して（CUDA 版は `.venv_cuda`）再ビルドする。
- ビルドしたサイドカーで読みがなを確かめる: `npm run dev-fast` は `.venv` を使うので、ここではビルド成果物を使う `npm run dev-ui` で起動し、設定の「ひらがなを表示」をオンにして日本語を送り、ふりがなが出ることを確認する。

- [ ] **Step 6: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest -q`
Expected: Task 0 と同じ結果＋追加分が passed

- [ ] **Step 7: Commit**

```powershell
git add src-python/ requirements.txt spec/
git commit -m "perf(transliteration): 読みがなの同梱辞書を full から core に切り替える"
```

---

### Task 9: full 辞書の後入れ（バックエンド）（§4 中盤）

**Files:**
- Create: `src-python/models/transliteration/transliteration_dictionary.py`
- Create: `src-python/test/test_transliteration_dictionary_download.py`
- Create: `src-python/test/test_controller_sudachi_dict.py`
- Modify: `src-python/config.py`（Selectable 系の宣言は 801 行付近、保存される設定は 969 行付近、既定値は `init_config()` の 1064 行付近と 1220 行付近）
- Modify: `src-python/errors.py:91-92` と `:397` 付近
- Modify: `src-python/model.py`（42 行付近の import、1547-1555 行）
- Modify: `src-python/controller.py`（`_SIMPLE_CONFIG_GETTERS` 226 行付近、`DownloadWhisper` の後 924 行付近、`getSelectableWhisperWeightTypeDict` の後 1383 行付近、`setWhisperWeightType` の後 3164 行付近、`downloadWhisperWeight` の後 3510 行付近、`init()` の `self.updateDownloadedWhisperModelWeight()` 呼び出し 4995 行付近）
- Modify: `src-python/mainloop.py`（`run_mapping` 95-97 行付近、`mapping` 450-458 行付近）

**Interfaces:**
- Consumes: Task 8 の `Transliterator(dict_path=...)`。`utils.isWeightVerifiedCache`、`utils.writeWeightVerifiedCache`、`utils.errorLogging`、`utils.printLog`
- Produces:
  - `transliteration_dictionary.SUDACHI_DICT_TYPES = ["core", "full"]`
  - `sudachiFullDictPath(root: str) -> str`（`<root>/weights/sudachi/full/system_full.dic`）
  - `checkSudachiFullDict(root: str) -> bool`
  - `downloadSudachiFullDict(root: str, callback: Callable[[float], None] | None = None, end_callback: Callable[[], None] | None = None) -> bool`
  - config: `SUDACHI_DICT_TYPE`（保存される。既定 `"core"`）、`SELECTABLE_SUDACHI_DICT_TYPE_LIST`（読み取り専用）、`SELECTABLE_SUDACHI_DICT_TYPE_DICT`（保存されない。`{"core": True, "full": bool}`）
  - `ErrorCode.WEIGHT_SUDACHI_DICT_DOWNLOAD`
  - model: `checkSudachiFullDict() -> bool`、`downloadSudachiFullDict(callback=None, end_callback=None) -> bool`、`restartTransliteration() -> None`
  - controller: `getSudachiDictType`（生成 getter）、`getSelectableSudachiDictTypeDict`、`setSudachiDictType(data)`、`downloadSudachiDict(data, asynchronous=True)`、`updateDownloadedSudachiDict()`
  - エンドポイント: `/get/data/selectable_sudachi_dict_type_dict`、`/get/data/selected_sudachi_dict_type`、`/set/data/selected_sudachi_dict_type`、`/run/download_sudachi_dict`。run_mapping のキーは `download_progress_sudachi_dict` / `downloaded_sudachi_dict` / `error_sudachi_dict`。進捗の payload は `{"weight_type": "full", "progress": 0.0-1.0}`（UI の `weight_download_status` テンプレートの形）。

- [ ] **Step 1: ダウンロード処理の失敗するテストを書く**

`src-python/test/test_transliteration_dictionary_download.py`:
```python
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from models.transliteration import transliteration_dictionary as sd


def _zipBytes(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def _fakeResponse(body: bytes) -> MagicMock:
    response = MagicMock()
    response.headers = {"content-length": str(len(body))}
    response.iter_content.return_value = [body[i:i + 1000] for i in range(0, len(body), 1000)]
    response.raise_for_status.return_value = None
    return response


class DownloadSudachiFullDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.payload = b"fake dictionary" * 100
        self.archive = _zipBytes(sd.SUDACHI_FULL_DICT_MEMBER, self.payload)

    def test_extracts_dictionary_and_reports_progress(self) -> None:
        progress = []
        end = MagicMock()
        with patch.object(sd, "SUDACHI_FULL_DICT_SHA256", hashlib.sha256(self.archive).hexdigest()), \
             patch.object(sd, "requests_get", return_value=_fakeResponse(self.archive)):
            ok = sd.downloadSudachiFullDict(self.root, callback=progress.append, end_callback=end)
        self.assertTrue(ok)
        with open(sd.sudachiFullDictPath(self.root), "rb") as f:
            self.assertEqual(f.read(), self.payload)
        self.assertTrue(sd.checkSudachiFullDict(self.root))
        self.assertAlmostEqual(progress[-1], 1.0)
        end.assert_called_once()
        self.assertFalse([n for n in os.listdir(os.path.dirname(sd.sudachiFullDictPath(self.root))) if n.endswith(".zip")])

    def test_hash_mismatch_leaves_no_dictionary(self) -> None:
        end = MagicMock()
        with patch.object(sd, "SUDACHI_FULL_DICT_SHA256", "0" * 64), \
             patch.object(sd, "requests_get", return_value=_fakeResponse(self.archive)), \
             patch.object(sd, "sleep"):
            ok = sd.downloadSudachiFullDict(self.root, end_callback=end)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(sd.sudachiFullDictPath(self.root)))
        self.assertFalse(sd.checkSudachiFullDict(self.root))
        end.assert_called_once()

    def test_network_error_is_retried_then_reported(self) -> None:
        with patch.object(sd, "requests_get", side_effect=OSError("offline")) as mock_get, \
             patch.object(sd, "sleep"):
            ok = sd.downloadSudachiFullDict(self.root)
        self.assertFalse(ok)
        self.assertEqual(mock_get.call_count, sd._DOWNLOAD_MAX_ATTEMPTS)

    def test_skips_download_when_already_verified(self) -> None:
        with patch.object(sd, "checkSudachiFullDict", return_value=True), \
             patch.object(sd, "requests_get") as mock_get:
            self.assertTrue(sd.downloadSudachiFullDict(self.root))
        mock_get.assert_not_called()

    def test_check_is_false_when_file_is_absent(self) -> None:
        self.assertFalse(sd.checkSudachiFullDict(self.root))
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_transliteration_dictionary_download.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: `transliteration_dictionary.py` を実装する**

```python
"""読みがな用 Sudachi の full 辞書の取得と確認。

標準は同梱の core 辞書 (SudachiDict-core)。full 辞書は 127MB の zip で
展開後 360MB あるため同梱せず、設定で選んだ人だけここで取得する
(A-1, 2026-09-23)。配布元は SudachiDict-full の setup.py と同じ CloudFront。
取得した zip は SHA256 を照合してから system_full.dic だけを取り出す。
"""

import hashlib
import os
import zipfile
from os import path as os_path
from time import sleep
from typing import Callable, Optional

from requests import get as requests_get

try:
    from utils import errorLogging, isWeightVerifiedCache, printLog, writeWeightVerifiedCache
except ImportError:
    import sys
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from utils import errorLogging, isWeightVerifiedCache, printLog, writeWeightVerifiedCache

SUDACHI_DICT_TYPES = ["core", "full"]
SUDACHI_FULL_DICT_URL = "https://d2ej7fkh96fzlu.cloudfront.net/sudachidict/sudachi-dictionary-20250825-full.zip"
SUDACHI_FULL_DICT_SHA256 = "7ce16d45e9ff0ccb70a3c8ba70346240ef246e6ad499cfab033fc2c1da4d19b4"
SUDACHI_FULL_DICT_MEMBER = "sudachi-dictionary-20250825/system_full.dic"

_DOWNLOAD_TIMEOUT = (10, 60)  # (connect, read) 秒
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF = 2  # 秒。attempt 番号を掛けて待機


def _fullDictDir(root: str) -> str:
    return os_path.join(root, "weights", "sudachi", "full")


def sudachiFullDictPath(root: str) -> str:
    return os_path.join(_fullDictDir(root), "system_full.dic")


def checkSudachiFullDict(root: str) -> bool:
    return os_path.isfile(sudachiFullDictPath(root)) and isWeightVerifiedCache(_fullDictDir(root))


def _removeQuietly(path: str) -> None:
    try:
        if os_path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _downloadArchive(zip_path: str, callback: Optional[Callable[[float], None]]) -> None:
    response = requests_get(SUDACHI_FULL_DICT_URL, stream=True, timeout=_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    file_size = int(response.headers.get("content-length", 0))
    digest = hashlib.sha256()
    received = 0
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 2000):
            f.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            if callback is not None and file_size > 0:
                callback(received / file_size)
    if digest.hexdigest() != SUDACHI_FULL_DICT_SHA256:
        raise ValueError("Sudachi full dictionary checksum mismatch")


def _extractDictionary(zip_path: str, dict_path: str) -> None:
    temporary_path = dict_path + ".tmp"
    with zipfile.ZipFile(zip_path) as archive, archive.open(SUDACHI_FULL_DICT_MEMBER) as source, \
            open(temporary_path, "wb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
    os.replace(temporary_path, dict_path)


def downloadSudachiFullDict(
    root: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> bool:
    if checkSudachiFullDict(root):
        return True
    directory = _fullDictDir(root)
    os.makedirs(directory, exist_ok=True)
    zip_path = os_path.join(directory, "sudachi-dictionary-full.zip")
    dict_path = sudachiFullDictPath(root)
    succeeded = False
    try:
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                _downloadArchive(zip_path, callback)
                _extractDictionary(zip_path, dict_path)
                writeWeightVerifiedCache(directory)
                succeeded = True
                break
            except Exception:
                errorLogging()
                _removeQuietly(dict_path + ".tmp")
                _removeQuietly(dict_path)
                if attempt < _DOWNLOAD_MAX_ATTEMPTS:
                    printLog(f"Sudachi full dictionary download failed, retrying ({attempt}/{_DOWNLOAD_MAX_ATTEMPTS - 1})")
                    sleep(_DOWNLOAD_RETRY_BACKOFF * attempt)
            finally:
                _removeQuietly(zip_path)
    finally:
        if end_callback is not None:
            end_callback()
    return succeeded
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_transliteration_dictionary_download.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit（ダウンロード処理）**

```powershell
git add src-python/models/transliteration/transliteration_dictionary.py src-python/test/test_transliteration_dictionary_download.py
git commit -m "feat(transliteration): 読みがなの full 辞書を取得・照合する処理を追加"
```

- [ ] **Step 6: controller・model の失敗するテストを書く**

`src-python/test/test_controller_sudachi_dict.py`:
```python
import unittest
from unittest.mock import MagicMock, patch

import controller as controller_module
import mainloop
from config import config
from controller import Controller
from errors import ErrorCode


class SudachiDictEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_type = config.SUDACHI_DICT_TYPE
        self._original_dict = dict(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        config.SUDACHI_DICT_TYPE = self._original_type
        config.SELECTABLE_SUDACHI_DICT_TYPE_DICT.update(self._original_dict)

    def test_endpoints_are_routed(self) -> None:
        for endpoint in (
            "/get/data/selectable_sudachi_dict_type_dict",
            "/get/data/selected_sudachi_dict_type",
            "/set/data/selected_sudachi_dict_type",
            "/run/download_sudachi_dict",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, mainloop.mapping)
        for key in ("download_progress_sudachi_dict", "downloaded_sudachi_dict", "error_sudachi_dict"):
            with self.subTest(run_mapping=key):
                self.assertIn(key, mainloop.run_mapping)

    def test_default_type_is_core(self) -> None:
        self.assertIn(config.SUDACHI_DICT_TYPE, ["core", "full"])
        self.assertEqual(config.SELECTABLE_SUDACHI_DICT_TYPE_LIST, ["core", "full"])
        self.assertTrue(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["core"])

    def test_set_type_restarts_transliteration(self) -> None:
        with patch.object(controller_module, "model") as mock_model:
            response = Controller.setSudachiDictType("full")
        self.assertEqual(response, {"status": 200, "result": "full"})
        mock_model.restartTransliteration.assert_called_once()

    def test_set_type_rejects_unknown_value(self) -> None:
        with patch.object(controller_module, "model"):
            response = Controller.setSudachiDictType("huge")
        self.assertNotEqual(response["status"], 200)

    def test_downloaded_marks_full_available(self) -> None:
        run = MagicMock()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = True
            handler = Controller.DownloadSudachiDict(mainloop.run_mapping, run)
            handler.downloaded()
        self.assertTrue(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"])
        run.assert_called_once_with(200, "/run/downloaded_sudachi_dict", "full")

    def test_failed_download_reports_error_code(self) -> None:
        run = MagicMock()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = False
            handler = Controller.DownloadSudachiDict(mainloop.run_mapping, run)
            handler.downloaded()
        status, endpoint, result = run.call_args.args
        self.assertEqual(endpoint, "/run/error_sudachi_dict")
        self.assertEqual(result["error_code"], ErrorCode.WEIGHT_SUDACHI_DICT_DOWNLOAD.value)

    def test_selectable_dict_reports_full_missing_after_deletion(self) -> None:
        config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"] = True
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = False
            Controller.updateDownloadedSudachiDict()
        self.assertFalse(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"])
```

`error_response["result"]` のキー名（`error_code`）と `ErrorCode` が Enum かどうかは、`errors.py` の `VRCTError.create_error_response` を読んで実物に合わせる。`Controller.setSudachiDictType` が staticmethod でなく instance method になる場合は `Controller.__new__(Controller)` で作って呼ぶ形にする（`test_controller_simple_config_getters.py` と同じ）。

- [ ] **Step 7: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_controller_sudachi_dict.py -q`
Expected: FAIL（`SUDACHI_DICT_TYPE` などが無い）

- [ ] **Step 8: config・errors を足す**

`config.py`:
- import に `from models.transliteration.transliteration_dictionary import SUDACHI_DICT_TYPES` を足す（whisper_models の import と同じ場所・同じ形）。
- 801 行付近（Selectable dict/list の並び）に:
```python
    SELECTABLE_SUDACHI_DICT_TYPE_LIST = ManagedProperty('SELECTABLE_SUDACHI_DICT_TYPE_LIST', readonly=True, serialize=False)
    SELECTABLE_SUDACHI_DICT_TYPE_DICT = ManagedProperty('SELECTABLE_SUDACHI_DICT_TYPE_DICT', type_=dict, serialize=False, mutable_tracking=True)
```
- 969 行付近（`WHISPER_WEIGHT_TYPE` の後）に:
```python
    SUDACHI_DICT_TYPE = ManagedProperty('SUDACHI_DICT_TYPE', type_=str, allowed=lambda v, inst: v in inst.SELECTABLE_SUDACHI_DICT_TYPE_LIST)
```
- `init_config()` の 1064-1066 行付近（`_SELECTABLE_WHISPER_WEIGHT_TYPE_DICT` の後）に:
```python
        self._SELECTABLE_SUDACHI_DICT_TYPE_LIST = list(SUDACHI_DICT_TYPES)
        self._SELECTABLE_SUDACHI_DICT_TYPE_DICT = {"core": True, "full": False}
```
- 1220 行付近（`self._WHISPER_WEIGHT_TYPE = "base"` の後）に `self._SUDACHI_DICT_TYPE = "core"`。

`errors.py`:
- 92 行の後に `WEIGHT_SUDACHI_DICT_DOWNLOAD = "WEIGHT_SUDACHI_DICT_DOWNLOAD"`。
- 397 行付近のメタデータに:
```python
    ErrorCode.WEIGHT_SUDACHI_DICT_DOWNLOAD: {"category": ErrorCategory.WEIGHT, "message": "Sudachi full dictionary download error", "severity": "error", "user_action_required": True},
```

- [ ] **Step 9: model を直す**

`model.py` の import 群（42 行付近）に:
```python
from models.transliteration.transliteration_dictionary import checkSudachiFullDict, downloadSudachiFullDict, sudachiFullDictPath
```
1547-1555 行を次にする:
```python
    def _transliterationDictPath(self):
        if config.SUDACHI_DICT_TYPE == "full" and checkSudachiFullDict(config.PATH_LOCAL):
            return sudachiFullDictPath(config.PATH_LOCAL)
        return None

    def startTransliteration(self):
        self.ensure_initialized()
        if self.transliterator is None:
            self.transliterator = Transliterator(dict_path=self._transliterationDictPath())

    def stopTransliteration(self):
        self.ensure_initialized()
        if self.transliterator is not None:
            self.transliterator = None

    def restartTransliteration(self):
        """辞書の種類を変えたとき、読みがなが有効なら新しい辞書で作り直す。"""
        self.ensure_initialized()
        if self.transliterator is not None:
            self.transliterator = Transliterator(dict_path=self._transliterationDictPath())

    def checkSudachiFullDict(self):
        return checkSudachiFullDict(config.PATH_LOCAL)

    def downloadSudachiFullDict(self, callback=None, end_callback=None):
        return downloadSudachiFullDict(config.PATH_LOCAL, callback, end_callback)
```

- [ ] **Step 10: controller を直す**

- `_SIMPLE_CONFIG_GETTERS` の `"getWhisperWeightType": "WHISPER_WEIGHT_TYPE",` の次に `"getSudachiDictType": "SUDACHI_DICT_TYPE",`。
- `DownloadWhisper` クラスの直後（924 行付近）に:
```python
    class DownloadSudachiDict:
        def __init__(self, run_mapping: dict, run: Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.run = run
            self._last_progress = -1.0
            self._last_time = 0.0

        def progressBar(self, progress) -> None:
            if not _shouldEmitDownloadProgress(self, progress):
                return
            printLog("Sudachi Full Dictionary Download Progress", progress)
            self.run(
                200,
                self.run_mapping["download_progress_sudachi_dict"],
                {"weight_type": "full", "progress": progress},
            )

        def downloaded(self) -> None:
            if model.checkSudachiFullDict() is True:
                config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"] = True
                self.run(200, self.run_mapping["downloaded_sudachi_dict"], "full")
            else:
                error_response = VRCTError.create_error_response(
                    ErrorCode.WEIGHT_SUDACHI_DICT_DOWNLOAD,
                    data=None
                )
                self.run(
                    error_response["status"],
                    self.run_mapping["error_sudachi_dict"],
                    error_response["result"],
                )
```
- `getSelectableWhisperWeightTypeDict` の直後（1383 行付近）に:
```python
    @staticmethod
    def getSelectableSudachiDictTypeDict(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_SUDACHI_DICT_TYPE_DICT}
```
- `setWhisperWeightType` の直後（3164 行付近）に:
```python
    @staticmethod
    @_configValidationErrorResponse(ErrorCode.VALIDATION_CONFIG_VALUE_INVALID)
    def setSudachiDictType(data, *args, **kwargs) -> dict:
        config.SUDACHI_DICT_TYPE = str(data)
        model.restartTransliteration()
        return {"status":200, "result": config.SUDACHI_DICT_TYPE}
```
- `downloadWhisperWeight` の直後（3510 行付近）に:
```python
    def downloadSudachiDict(self, data: str = "full", asynchronous: bool = True, *args, **kwargs) -> dict:
        handler = self.DownloadSudachiDict(self.run_mapping, self.run)
        if asynchronous is True:
            th_download = Thread(
                target=model.downloadSudachiFullDict,
                args=(handler.progressBar, handler.downloaded),
                daemon=True,
            )
            th_download.start()
        else:
            model.downloadSudachiFullDict(handler.progressBar, handler.downloaded)
        return {"status":200, "result":True}

    @staticmethod
    def updateDownloadedSudachiDict() -> None:
        config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"] = model.checkSudachiFullDict()
```
- `init()` の `self.updateDownloadedWhisperModelWeight()`（4995 行付近）の直後に `self.updateDownloadedSudachiDict()`。これは読みがなの開始（4998-5001 行）より前に来ること。

`Thread`・`Callable`・`Any`・`VRCTError`・`printLog` が controller.py で既に import 済みか確かめ、無いものだけ足す。

- [ ] **Step 11: mainloop を直す**

`run_mapping` の `error_whisper_weight` の次に:
```python
    "download_progress_sudachi_dict":"/run/download_progress_sudachi_dict",
    "downloaded_sudachi_dict":"/run/downloaded_sudachi_dict",
    "error_sudachi_dict":"/run/error_sudachi_dict",
```
`mapping` の `/run/download_whisper_weight` の次に:
```python
    "/get/data/selectable_sudachi_dict_type_dict": {"status": True, "variable":controller.getSelectableSudachiDictTypeDict},
    "/get/data/selected_sudachi_dict_type": {"status": True, "variable":controller.getSudachiDictType},
    "/set/data/selected_sudachi_dict_type": {"status": True, "variable":controller.setSudachiDictType},
    "/run/download_sudachi_dict": {"status": True, "variable":controller.downloadSudachiDict},
```

- [ ] **Step 12: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest -q`
Expected: Task 0 と同じ結果＋追加分が passed（`test_ui_endpoint_contract.py` も通る。UI 側の宣言は Task 10 で足す）

- [ ] **Step 13: Commit**

```powershell
git add src-python/
git commit -m "feat(transliteration): 読みがなの辞書種別の設定と full 辞書ダウンロードのエンドポイントを追加"
```

---

### Task 10: full 辞書の後入れ（UI）（§4 後半）

**Files:**
- Modify: `src-ui/logics/ui_configs.js`（`whisper_weight_type_status` の後、100 行付近）
- Modify: `src-ui/logics/configs/config_page_setter/ui_config_setter.js`（`ConvertMessageToHiragana` の宣言 733-748 行付近の後）
- Modify: `src-ui/views/app/config_page/setting_section/setting_box/others/Others.jsx`（53-56 行と末尾）
- Modify: `src-ui/logics/_useBackendErrorHandling.js:142-144`
- Modify: `locales/ja.yml`、`locales/en.yml`、`locales/ko.yml`、`locales/zh-Hans.yml`、`locales/zh-Hant.yml`

**Interfaces:**
- Consumes: Task 9 のエンドポイントと run_mapping のルート
- Produces: `useOthers()` に `currentSudachiDictTypeStatus`、`pendingSudachiDictTypeStatus`、`downloadSudachiDictTypeStatus`、`currentSelectedSudachiDictType`、`setSelectedSudachiDictType` が生える（設定宣言からの自動生成。名前は Base_Name から決まる）。

- [ ] **Step 1: UI の宣言を足す（契約テストが先に落ちることを確かめる）**

`ui_config_setter.js` の `ConvertMessageToHiragana` 宣言の直後に:
```js
    {
        Category: "Others",
        Base_Name: "SudachiDictTypeStatus",
        default_value: sudachi_dict_type_status,
        ui_template_id: "list",
        logics_template_id: "weight_download_status",
        base_endpoint_name: "sudachi_dict",
    },
    {
        Category: "Others",
        Base_Name: "SelectedSudachiDictType",
        default_value: "core",
        ui_template_id: "select",
        logics_template_id: "get_set",
        base_endpoint_name: "selected_sudachi_dict_type",
    },
```
同じファイルの `whisper_weight_type_status` を import している行に `sudachi_dict_type_status` を足す。

Run: `.venv\Scripts\python -m pytest src-python/test/test_ui_endpoint_contract.py -q`
Expected: passed（Task 9 でエンドポイントを足してあるため）。落ちたら、エンドポイント名の綴りを Task 9 と突き合わせる。

- [ ] **Step 2: 既定値を足す**

`ui_configs.js` の `whisper_weight_type_status` の後に:
```js
export const sudachi_dict_type_status = [
    { id: "core", capacity: "", is_default: true },
    { id: "full", capacity: "127MB" },
].map(item => ({ is_default: false, ...item, is_downloaded: false, progress: null }));
```

- [ ] **Step 3: 設定画面に出す**

`Others.jsx` の import に `DownloadModelsContainer` を足す:
```js
import {
    CheckboxContainer,
    MessageFormatContainer,
    DownloadModelsContainer,
} from "../_templates/Templates";
```
53-56 行を次にする:
```jsx
            <div>
                <ConvertMessageToRomajiContainer />
                <ConvertMessageToHiraganaContainer />
                <SudachiDictTypeContainer />
            </div>
```
`ConvertMessageToHiraganaContainer` の後に:
```jsx
const SudachiDictTypeContainer = () => {
    const { t } = useI18n();
    const {
        currentSudachiDictTypeStatus,
        pendingSudachiDictTypeStatus,
        downloadSudachiDictTypeStatus,
        currentSelectedSudachiDictType,
        setSelectedSudachiDictType,
    } = useOthers();

    const downloadStartFunction = (id) => {
        pendingSudachiDictTypeStatus(id);
        downloadSudachiDictTypeStatus(id);
    };

    const options = currentSudachiDictTypeStatus.data.map(item => ({
        ...item,
        label: t(`config_page.others.sudachi_dict_type.${item.id}`),
    }));

    return (
        <DownloadModelsContainer
            label={t("config_page.others.sudachi_dict_type.label")}
            desc={t("config_page.others.sudachi_dict_type.desc")}
            name="sudachi_dict_type"
            options={options}
            checked_variable={currentSelectedSudachiDictType}
            selectFunction={setSelectedSudachiDictType}
            downloadStartFunction={downloadStartFunction}
        />
    );
};
```

- [ ] **Step 4: エラー表示を足す**

`_useBackendErrorHandling.js` の `case "WEIGHT_WHISPER_DOWNLOAD":` ブロックの後に:
```js
            case "WEIGHT_SUDACHI_DICT_DOWNLOAD":
                showNotification_Error(t("common_error.failed_download_sudachi_dict"), { category_id: error_code });
                return;
```

- [ ] **Step 5: 文言を足す**

`locales/ja.yml`:
- `common_error` の `failed_download_weight_whisper` の次の行に: `    failed_download_sudachi_dict: "読みがな用の辞書のダウンロードに失敗しました。"`
- `config_page.others` の `convert_message_to_hiragana:` ブロックの後（`telemetry:` の前）に、同じインデントで:
```yaml
        sudachi_dict_type:
            label: "読みがなの辞書"
            desc: "「full」は人名・地名・新しい言葉の読みがより正確になります。初回だけダウンロード（約127MB、展開後は約360MB）が必要です。"
            core: "標準"
            full: "full（高精度）"
```
`locales/en.yml` の同じ位置に:
```yaml
    failed_download_sudachi_dict: "Failed to download the reading dictionary."
```
```yaml
        sudachi_dict_type:
            label: "Reading Dictionary"
            desc: "\"full\" gives more accurate readings for names, places and newer words. It needs a one-time download (about 127MB, about 360MB after extraction)."
            core: "Standard"
            full: "full (more accurate)"
```
`ko.yml`、`zh-Hans.yml`、`zh-Hant.yml` には en と同じ英語の文言を同じ位置に入れる。

- [ ] **Step 6: ビルドと実機確認**

```powershell
npm run vite-build
npm run dev-fast
```
確認すること:
1. 設定 → その他に「読みがなの辞書」が出て、「標準」が選ばれている。
2. 「full（高精度）」のダウンロードを押すと進捗が出て、終わると選べるようになる。
3. full を選んで「ひらがなを表示」をオンにし、日本語を送るとふりがなが出る。
4. アプリを閉じ、`src-python\weights\sudachi\full\system_full.dic` を消して再起動すると、full がダウンロード前の表示に戻り、ふりがなは core で出る。
5. ネットワークを切ってダウンロードを押すと、エラー通知「読みがな用の辞書のダウンロードに失敗しました。」が出る。

- [ ] **Step 7: Commit**

```powershell
git add src-ui/ locales/
git commit -m "feat(ui): 設定に読みがなの辞書（標準 / full）の選択とダウンロードを追加"
```

---

### Task 11: import の遅延化（§5）

**Files:**
- Modify: `src-python/utils.py:74-86`
- Modify: `src-python/models/translation/translation_translator.py`（`ctranslate2` の import 部）
- Modify: `src-python/models/translation/translation_utils.py:29-32`
- Modify: `src-python/models/transcription/transcription_whisper.py:29-32`
- Modify: 計測で対象に選ばれたその他のモジュール
- Create: `src-python/test/test_lazy_imports.py`

**Interfaces:**
- Consumes: Task 1 の計測ツール
- Produces: `utils._ct2_get_supported_compute_types(device, device_index)` と `utils._ct2_get_cuda_device_count()` は関数として残す（既存テストが patch する）。`translation_translator.ctranslate2` と `translation_utils.ctranslate2` はモジュール属性として残し、初期値は None。使う側は `_ctranslate2()` 経由で取る。

- [ ] **Step 1: 起動時の import 上位を測る**

```powershell
$env:VRCT_IMPORTTIME = Join-Path $env:TEMP "vrct-importtime.txt"
cd src-python
..\.venv\Scripts\python -X importtime -c "import mainloop" 2> $env:VRCT_IMPORTTIME
cd ..
.venv\Scripts\python -c "import importlib.util,os; s=importlib.util.spec_from_file_location('m','tools/measure_footprint.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); [print(e) for e in m.parseImportTime(open(os.environ['VRCT_IMPORTTIME'],encoding='utf-8').read(), 30)]"
```
対象の選び方: 累積 50 ms 以上で、`controller.init()` の中で必ず使うもの以外。
- 必ず使う: `yaml`、`requests`、`psutil`、`pythonosc`、`websockets`（起動時に使う）
- 候補: `ctranslate2`、`faster_whisper`、`rapidocr`、`cv2`、`openvr`、`OpenGL`、`glfw`、`sudachipy`、`google.genai`、`openai`、`deepl`、`translators`、`pydub`、`onnxruntime`
選んだ一覧と各累積時間を控えておく（Step 7 で README に書く）。

- [ ] **Step 2: 失敗するテストを書く**

`src-python/test/test_lazy_imports.py`:
```python
"""重いモジュールが mainloop の import だけでは読み込まれないこと。

別プロセスで `import mainloop` だけを行い、sys.modules を調べる。
"""

import json
import os
import subprocess
import sys
import unittest

_SRC_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Step 1 の計測で選んだモジュールを並べる。ctranslate2 と faster_whisper は必ず入れる。
_LAZY_MODULES = ["ctranslate2", "faster_whisper"]


class LazyImportTests(unittest.TestCase):
    def test_heavy_modules_are_not_loaded_at_startup(self) -> None:
        code = (
            "import sys, json; import mainloop; "
            f"print(json.dumps([m for m in {_LAZY_MODULES!r} if m in sys.modules]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=_SRC_PYTHON,
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        loaded = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, [])
```
`_LAZY_MODULES` に Step 1 で選んだモジュールを足す（トップレベル名。例 `"rapidocr"`、`"cv2"`）。

- [ ] **Step 3: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_lazy_imports.py -q`
Expected: FAIL（`ctranslate2` などが読み込まれている）

- [ ] **Step 4: `utils.py` の ctranslate2 を遅延させる**

74-86 行を次にする:
```python
# ctranslate2 は import に時間がかかるので、使うときに読み込む (A-1, 2026-09-23)。
# 関数名はテストが patch するので変えない。ctranslate2 が無い環境では空/0 を返す。
def _ct2_get_supported_compute_types(device: str, device_index: int) -> List[str]:
    try:
        from ctranslate2 import get_supported_compute_types
    except Exception:
        return []
    return get_supported_compute_types(device, device_index)


def _ct2_get_cuda_device_count() -> int:
    try:
        from ctranslate2 import get_cuda_device_count
    except Exception:
        return 0
    return get_cuda_device_count()
```

- [ ] **Step 5: モジュール属性を持つ import を遅延させる**

`translation_translator.py` の `ctranslate2` の try/except を次にする:
```python
# 使うときに _ctranslate2() が読み込む (起動時間の短縮, A-1)。テストはこの属性を差し替える。
ctranslate2 = None


def _ctranslate2():
    global ctranslate2
    if ctranslate2 is None:
        try:
            import ctranslate2 as _module
        except Exception:
            return None
        ctranslate2 = _module
    return ctranslate2
```
そのうえで `changeCTranslate2Model` の `if ctranslate2 is None: return` を
```python
        ct2 = _ctranslate2()
        if ct2 is None:
            return
```
にし、同じメソッド内の `ctranslate2.Translator(` を `ct2.Translator(` にする。ファイル内のほかの `ctranslate2.` 参照も同じく `_ctranslate2()` から取った値を使う。

`translation_utils.py` の 29-32 行にも同じ `ctranslate2 = None` と `_ctranslate2()` を置き、`checkCTranslate2Weight` を
```python
        ct2 = _ctranslate2()
        if ct2 is None:
            return False
        compute_type = getBestComputeType("cpu", 0)
        ct2.Translator(path, compute_type=compute_type)
```
にする。

`transcription_whisper.py` の 29-32 行（`WhisperModel`）も同じ形にする:
```python
WhisperModel = None  # 使うときに _whisperModelClass() が読み込む (A-1)


def _whisperModelClass():
    global WhisperModel
    if WhisperModel is None:
        try:
            from faster_whisper import WhisperModel as _cls
        except Exception:
            return None
        WhisperModel = _cls
    return WhisperModel
```
ファイル内の `if WhisperModel is None:` と `WhisperModel(` の参照を、`cls = _whisperModelClass()` を取ってから `if cls is None:`・`cls(` を使う形にする。`getWhisperModel` など同ファイルのほかの関数も同様。

`audio_vad.py:126` の `from faster_whisper.vad import get_vad_model` は既に関数内なので変えない。

- [ ] **Step 6: Step 1 で選んだ残りのモジュールを遅延させる**

各モジュールについて、トップレベルの `import X` / `from X import Y` を使用箇所の関数内へ移す。有無の判定だけに使っている箇所は `importlib.util.find_spec("X") is not None` に置き換える。移したら `test_lazy_imports.py` の `_LAZY_MODULES` に足してあることを確かめ、既存テストが patch している名前（`patch.object(module, "X")` の `X`）が消えていないかを `Select-String -Path src-python\test\*.py -Pattern 'patch.object\(<module>, "X"'` で確認する。消えた名前があれば、その名前をモジュール属性として残す（上の `ctranslate2 = None` と同じ形）。

- [ ] **Step 7: テストと計測**

Run: `.venv\Scripts\python -m pytest -q`
Expected: Task 0 と同じ結果＋追加分が passed

```powershell
bat\build.bat
Get-ChildItem src-tauri\bin\_internal -Directory | Where-Object Name -in 'ctranslate2','faster_whisper','rapidocr','cv2' | Select-Object Name
.venv\Scripts\python tools\measure_footprint.py --label after-lazy-import-cpu --bin-dir src-tauri\bin --python .venv\Scripts\python.exe
```
Expected: 遅延させたモジュールも `_internal` に収集されている（PyInstaller は関数内の import も拾う）。無いものがあれば `spec/backend.spec` と `spec/backend_cuda.spec` の `hiddenimports` に足して再ビルドする。
`docs/perf/README.md` に「遅延 import 後（CPU）」の行と、遅延させたモジュールの一覧（Step 1 の累積時間つき）を足す。

- [ ] **Step 8: 実機で確かめる**

`npm run dev-ui`（ビルド成果物）で起動し、Whisper の文字起こし・CTranslate2 翻訳・OCR（使える環境なら）がそれぞれ初回使用時に動くことを確認する。

- [ ] **Step 9: Commit**

```powershell
git add src-python/ spec/ docs/perf/
git commit -m "perf: 起動時に使わない重いモジュールの import を使用時まで遅らせる"
```

---

### Task 12: 外部フォルダの CUDA DLL を読めるようにする（§7）

**Files:**
- Modify: `src-python/utils.py:19-59`
- Create: `src-python/test/test_utils_cuda_library_dirs.py`

**Interfaces:**
- Consumes: なし
- Produces: `utils.externalCudaLibraryDir() -> str | None`（`%LOCALAPPDATA%\VRCT\cuda\bin` が存在すればそのパス）、`utils._cudaLibraryDirs() -> list[str]`、`utils._registerCudaLibraries() -> None`（旧 `_registerBundledCudaLibraries`）。インストーラーのサブプロジェクトが `externalCudaLibraryDir` の場所に DLL を置く。

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_utils_cuda_library_dirs.py`:
```python
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import utils


class CudaLibraryDirsTests(unittest.TestCase):
    def test_external_dir_is_used_when_it_exists(self) -> None:
        with tempfile.TemporaryDirectory() as local_app_data:
            external = os.path.join(local_app_data, "VRCT", "cuda", "bin")
            os.makedirs(external)
            with patch.dict(os.environ, {"LOCALAPPDATA": local_app_data}), \
                 patch.dict(sys.modules, {"nvidia": None}):
                self.assertEqual(utils.externalCudaLibraryDir(), external)
                self.assertEqual(utils._cudaLibraryDirs(), [external])

    def test_external_dir_is_ignored_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as local_app_data:
            with patch.dict(os.environ, {"LOCALAPPDATA": local_app_data}), \
                 patch.dict(sys.modules, {"nvidia": None}):
                self.assertIsNone(utils.externalCudaLibraryDir())
                self.assertEqual(utils._cudaLibraryDirs(), [])

    def test_bundled_dirs_come_before_external(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bundled = os.path.join(root, "nvidia", "cublas", "bin")
            os.makedirs(bundled)
            external = os.path.join(root, "local", "VRCT", "cuda", "bin")
            os.makedirs(external)
            fake_nvidia = types.ModuleType("nvidia")
            fake_nvidia.__path__ = [os.path.join(root, "nvidia")]
            with patch.dict(os.environ, {"LOCALAPPDATA": os.path.join(root, "local")}), \
                 patch.dict(sys.modules, {"nvidia": fake_nvidia}):
                self.assertEqual(utils._cudaLibraryDirs(), [bundled, external])

    @unittest.skipUnless(os.name == "nt", "DLL search path registration is Windows-only")
    def test_register_adds_dll_directories_and_path(self) -> None:
        with patch.object(utils, "_cudaLibraryDirs", return_value=[r"C:\cuda\bin"]), \
             patch.object(utils.os, "add_dll_directory") as mock_add, \
             patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            utils._registerCudaLibraries()
            self.assertTrue(os.environ["PATH"].startswith(r"C:\cuda\bin" + os.pathsep))
        mock_add.assert_called_once_with(r"C:\cuda\bin")
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_utils_cuda_library_dirs.py -q`
Expected: FAIL（`externalCudaLibraryDir` が無い）

- [ ] **Step 3: 実装する**

`utils.py` の 19-59 行を次にする（docstring の既存の説明は残し、外部フォルダの段落を足す）:
```python
def externalCudaLibraryDir() -> Optional[str]:
    """インストーラーが後から入れた CUDA ライブラリの置き場所 (存在すれば)。

    CPU版とCUDA版を1つのビルドにするため、cuBLAS / cuDNN を同梱せず
    %LOCALAPPDATA%\\VRCT\\cuda\\bin に置けるようにした (A-1, 2026-09-23)。
    取得処理はインストーラー側のサブプロジェクトで作る。
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    path = os.path.join(local_app_data, "VRCT", "cuda", "bin")
    return path if os.path.isdir(path) else None


def _cudaLibraryDirs() -> List[str]:
    library_dirs: List[str] = []
    try:
        import nvidia  # type: ignore
    except ImportError:
        nvidia = None
    if nvidia is not None:
        library_dirs.extend(sorted(
            {
                library_dir
                for search_path in nvidia.__path__
                for library_dir in glob.glob(os.path.join(search_path, "*", "bin"))
                if os.path.isdir(library_dir)
            }
        ))
    external = externalCudaLibraryDir()
    if external is not None:
        library_dirs.append(external)
    return library_dirs


def _registerCudaLibraries() -> None:
    """CUDAライブラリをDLL検索パスへ登録する。

    <既存 _registerBundledCudaLibraries の docstring 本文をここへそのまま移す>

    同梱 (CUDA版ビルド) に加えて externalCudaLibraryDir() も登録する。
    どちらも無ければ何もしない。
    """
    if os.name != "nt":
        return
    library_dirs = _cudaLibraryDirs()
    if not library_dirs:
        return
    for library_dir in library_dirs:
        os.add_dll_directory(library_dir)
    os.environ["PATH"] = os.pathsep.join(library_dirs) + os.pathsep + os.environ.get("PATH", "")

_registerCudaLibraries()
```
`<...>` の行は、既存 docstring（20-36 行）の本文で置き換える（`_registerBundledCudaLibraries` という名前への言及があれば新しい名前に直す）。`Optional` と `List` は既に `typing` から import 済み。

`_registerBundledCudaLibraries` をほかで参照していないか確かめる:
```powershell
Select-String -Path src-python\*.py,src-python\models\**\*.py,src-python\test\*.py,tools\*.py -Pattern '_registerBundledCudaLibraries'
```
Expected: 出ない（出たら新しい名前に直す）。

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_utils_cuda_library_dirs.py src-python/test/test_utils_compute_device.py -q`
Expected: すべて passed

- [ ] **Step 5: 実機で確かめる（NVIDIA GPU がある環境のみ）**

```powershell
bat\build.bat
$dest = Join-Path $env:LOCALAPPDATA "VRCT\cuda\bin"
New-Item -ItemType Directory -Force $dest | Out-Null
Get-ChildItem .venv_cuda\Lib\site-packages\nvidia\*\bin\*.dll | Copy-Item -Destination $dest
npm run dev-ui
```
確認すること: 設定の文字起こし・翻訳の計算デバイスに GPU が出て、GPU を選ぶと Whisper と CTranslate2 が動く。終わったら `Remove-Item -Recurse (Join-Path $env:LOCALAPPDATA "VRCT\cuda")` で片付け、GPU が一覧から消えることも確かめる。GPU が無い環境ではこの手順を飛ばし、飛ばしたことをコミットメッセージの本文に書く。

- [ ] **Step 6: Commit**

```powershell
git add src-python/utils.py src-python/test/test_utils_cuda_library_dirs.py
git commit -m "feat: CUDA ライブラリを %LOCALAPPDATA%\VRCT\cuda\bin からも読み込めるようにする"
```

---

### Task 13: 最終計測とまとめ

**Files:**
- Create: `docs/perf/final-cpu-<日付>.json`、`docs/perf/final-cuda-<日付>.json`（ツールが作る）
- Modify: `docs/perf/README.md`

**Interfaces:**
- Consumes: すべての前タスク
- Produces: 基準値との比較表

- [ ] **Step 1: 全テスト**

Run: `.venv\Scripts\python -m pytest -q -rs`
Expected: Task 0 の既知の失敗以外はすべて passed。スキップは理由を確認する（照合テストはスキップされないこと）。

- [ ] **Step 2: CPU 版と CUDA 版を計測する**

```powershell
bat\build.bat
npm run vite-build
npm run tauri build
$installer = (Get-ChildItem src-tauri\target\release\bundle\nsis\*.exe | Select-Object -First 1).FullName
.venv\Scripts\python tools\measure_footprint.py --label final-cpu --bin-dir src-tauri\bin --installer $installer --python .venv\Scripts\python.exe
bat\build_cuda.bat
npm run tauri build
$installer = (Get-ChildItem src-tauri\target\release\bundle\nsis\*.exe | Select-Object -First 1).FullName
.venv_cuda\Scripts\python tools\measure_footprint.py --label final-cuda --bin-dir src-tauri\bin --installer $installer --python .venv_cuda\Scripts\python.exe
```

- [ ] **Step 3: 比較表を書く**

`docs/perf/README.md` の末尾に:
```markdown
## 結果（A-1 完了時）

| 版 | 指標 | 基準値 | 最終 | 差 |
|---|---|---|---|---|
| CPU | bin 合計 | <MB> | <MB> | <-x MB (-y%)> |
| CPU | インストーラー | ... | ... | ... |
| CPU | 起動（中央値） | <秒> | <秒> | ... |
| CPU | アイドル RSS | ... | ... | ... |
| CPU | モデル読込後 RSS | ... | ... | ... |
| CUDA | （同じ 5 行） | ... | ... | ... |

### 効いた変更
- <変更>: <どの指標が何 MB / 何秒 変わったか。途中計測（after-*.json）から読み取る>

### 悪化した指標
- <±5% を超えて悪化したものがあれば、原因と対処。無ければ「なし」>
```
±5% を超える悪化がある場合は、ここで止まって報告する（Global Constraints に反するため）。

- [ ] **Step 4: 実機での通し確認**

CPU 版のインストーラーでインストールし（既存の VRCT が入っていれば事前にバックアップ）、次を一通り動かす:
1. マイクの文字起こし（Whisper）
2. API 翻訳（キーがあるもの 1 つ）
3. CTranslate2 翻訳
4. 読みがな（core と full の両方）
5. OCR（使える環境なら）

動かせなかった項目は README の「結果」節に理由とともに書く。

- [ ] **Step 5: Commit**

```powershell
git add docs/perf/
git commit -m "docs(perf): A-1 の最終計測と基準値との比較を記録"
```
