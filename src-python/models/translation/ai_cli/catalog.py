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
# エフォート (考える量)。codex はモデルごとに `codex debug models` が返す値、
# claude は `claude --effort` が受け付ける値に、推論しない none (`--thinking disabled`。
# ヘルプに無いフラグで、claude 2.1.282 は enabled / adaptive / disabled を受け付ける) を足したもの。
# agy はモデル名にエフォートが入っている (gemini-...-high など) ので選ばせない。
CLAUDE_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT = "low"
_codex_efforts: dict[str, list[str]] = {}
# codex の Fast モード。`codex debug models` の service_tiers で名前が Fast の段の id
# (codex 0.155.1 では "priority")。app-server の turn/start の serviceTier に渡す。
_codex_fast_tiers: dict[str, str] = {}
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
            slugs = []
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("slug"):
                    continue
                slug = str(entry["slug"])
                levels = entry.get("supported_reasoning_levels") or []
                _codex_efforts[slug] = [
                    str(level.get("effort") if isinstance(level, dict) else level)
                    for level in levels if (level.get("effort") if isinstance(level, dict) else level)
                ]
                fast = [tier.get("id") for tier in entry.get("service_tiers") or []
                        if isinstance(tier, dict) and str(tier.get("name", "")).lower() == "fast" and tier.get("id")]
                if fast:
                    _codex_fast_tiers[slug] = str(fast[0])
                else:
                    _codex_fast_tiers.pop(slug, None)
                slugs.append(slug)
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


def listEfforts(tool: str, model: str) -> list[str]:
    """その CLI とモデルで選べるエフォート。選べないなら []。codex は listModels() の後で分かる。"""
    if tool == "claude":
        return list(CLAUDE_EFFORTS)
    if tool == "codex":
        return list(_codex_efforts.get(model, []))
    return []


def fastTier(tool: str, model: str):
    """Fast モードの service tier の id。そのモデルに無ければ None (codex 以外も None)。"""
    if tool != "codex":
        return None
    return _codex_fast_tiers.get(model)


def effectiveEffort(preferred, efforts: list[str]):
    """選んでいたエフォートが使えればそれ、使えなければ low (無ければ先頭)。選べないなら None。"""
    if not efforts:
        return None
    if preferred in efforts:
        return preferred
    return DEFAULT_EFFORT if DEFAULT_EFFORT in efforts else efforts[0]
