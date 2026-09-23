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
