"""導入済みの AI CLI とモデル一覧を調べる。"""

import json
import shutil
import subprocess

try:
    from .base import CliSession
    from utils import errorLogging
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__))))))
    from models.translation.ai_cli.base import CliSession
    from utils import errorLogging

TOOLS = ("codex", "claude", "agy")
# claude にはモデル一覧を返すコマンドが無いので別名を固定で並べる。
CLAUDE_MODELS = ["haiku", "sonnet", "opus"]
_LIST_TIMEOUT_SEC = 30
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# codex は npm のシム (codex.cmd / codex.ps1) で入るので .cmd を優先する。
_EXECUTABLE_NAMES = {"codex": ("codex.cmd", "codex"), "claude": ("claude",), "agy": ("agy",)}


def resolveExecutable(tool: str):
    for name in _EXECUTABLE_NAMES.get(tool, ()):
        path = shutil.which(name)
        if path:
            return path
    return None


def detectInstalledTools() -> list[str]:
    return [tool for tool in TOOLS if resolveExecutable(tool)]


def _run(executable: str, args: list[str], timeout: float = _LIST_TIMEOUT_SEC) -> str:
    """コマンドを実行して標準出力を返す。締め切りを過ぎたら子プロセスごと止める。

    `subprocess.run(timeout=...)` は Windows で `codex.cmd` を止め切れない:
    殺すのは cmd.exe だけで、その後の communicate() が、パイプを握ったままの
    孫プロセス (node) の終了を無期限に待つ。そこで Popen + communicate(timeout)
    にして、締め切りを過ぎたら taskkill /T で木ごと止める。
    """
    proc = subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=_CREATE_NO_WINDOW)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        CliSession._kill(proc)
        try:
            # 木ごと止めたのでパイプはすぐ閉じる。残った出力を捨ててパイプを片付ける。
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise RuntimeError(f"{executable} {' '.join(args)} did not finish in {timeout} seconds") from None
    if proc.returncode != 0:
        output = (stderr or stdout or "")[:200]
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {output}")
    return stdout or ""


def listModels(tool: str) -> list[str]:
    if tool == "claude":
        return list(CLAUDE_MODELS)
    executable = resolveExecutable(tool)
    if executable is None:
        return []
    try:
        if tool == "codex":
            data = json.loads(_run(executable, ["debug", "models"]))
            entries = data.get("models", []) if isinstance(data, dict) else data
            slugs = [str(entry.get("slug")) for entry in entries if isinstance(entry, dict) and entry.get("slug")]
            return [slug for slug in slugs if "review" not in slug]
        if tool == "agy":
            models = []
            for line in _run(executable, ["models"]).splitlines():
                if "\t" in line:
                    model_id = line.split("\t", 1)[0].strip()
                    if model_id:
                        models.append(model_id)
            return models
    except Exception:
        errorLogging()
    return []
