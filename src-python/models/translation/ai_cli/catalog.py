"""導入済みの AI CLI とモデル一覧を調べる。"""

import json
import shutil
import subprocess

try:
    from utils import errorLogging
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__))))))
    from utils import errorLogging

TOOLS = ("codex", "claude", "agy")
# claude にはモデル一覧を返すコマンドが無いので別名を固定で並べ。
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


def _run(executable: str, args: list[str]) -> str:
    result = subprocess.run([executable, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=_LIST_TIMEOUT_SEC, creationflags=_CREATE_NO_WINDOW)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "")[:200]
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {output}")
    return result.stdout or ""


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
