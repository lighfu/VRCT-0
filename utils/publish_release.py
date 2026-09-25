"""VRCT-0 を公開する: 版を決めてコミットし、タグを付けて push し、CI の公開を見届ける。

版はリリースした日付 (utils/update_version.py)。安定版は 2026.9.25、ベータ版は 2026.9.25-beta.1。
タグ v<版> を push すると .github/workflows/release.yml がビルドして GitHub Releases に公開する。

    python utils/publish_release.py                  今日の日付で安定版を出す
    python utils/publish_release.py --beta 1         今日の日付のベータ版 1 を出す
    python utils/publish_release.py --dry-run        何をするかだけ表示する (何も変えない)

オプション:
    --date YYYY-MM-DD   版にする日付 (省くと今日)
    --skip-tests        公開前のテスト (Python のテストと UI のビルド) を省く
    --yes               push の前の確認を省く
    --no-watch          CI の完了を待たない
    --trailer TEXT      リリースのコミットの末尾に付ける行 (何度でも指定できる)

手元でビルドしてパッケージを作るだけなら npm run release (utils/pack_release.py) を使う。
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_BRANCH = "develop"
REMOTE = "origin"
REPO = "lighfu/VRCT-0"
WORKFLOW = "release.yml"
BUILD_CHANNEL_FILE = Path("src-python") / "build_channel.py"
VERSION_FILES = [
    "package.json",
    "package-lock.json",
    "src-tauri/tauri.conf.json",
    "src-python/config.py",
    str(BUILD_CHANNEL_FILE).replace("\\", "/"),
]
# 公開されたリリースに無ければならないファイル (Velopack の vpk upload が上げるもの)。
REQUIRED_ASSET_PATTERNS = [
    re.compile(r"^VRCT-0-win-Setup\.exe$"),
    re.compile(r"^releases\.win\.json$"),
    re.compile(r"^VRCT-0-.+-full\.nupkg$"),
]

_spec = importlib.util.spec_from_file_location("update_version", ROOT / "utils" / "update_version.py")
update_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_version)


class ReleaseError(Exception):
    """公開を始められない・続けられないとき。メッセージはそのまま画面に出す。"""


# ---- 版 ----------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)\.(\d+))?$")


def parseVersion(version: str):
    """比べられる形にする。安定版はその日のベータ版より新しい。読めなければ None。"""
    match = _VERSION_RE.match(version)
    if match is None:
        return None
    year, month, day, pre, number = match.groups()
    # 安定版は (…, 1, 0)、ベータ版・rc は (…, 0, 番号) にして、同じ日なら安定版が上になるようにする。
    return (int(year), int(month), int(day), 0 if pre else 1, int(number) if number else 0)


def isNewer(version: str, other: str) -> bool:
    return parseVersion(version) > parseVersion(other)


def channelOf(version: str) -> str:
    return "beta" if "-" in version else "stable"


def buildChannelContent(content: str, channel: str) -> str:
    new_content, count = re.subn(
        r'^(BUILD_CHANNEL = ")[^"]+(")', rf"\g<1>{channel}\g<2>", content, count=1, flags=re.M
    )
    if count != 1:
        raise ReleaseError(f"{BUILD_CHANNEL_FILE} に BUILD_CHANNEL の行が見つかりません")
    return new_content


# ---- 外のコマンド ------------------------------------------------------------

def run(args, check=True, capture=True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise ReleaseError(f"{' '.join(args)} が失敗しました (終了コード {result.returncode})\n{detail}")
    return result


def git(*args, check=True) -> str:
    return run(["git", *args], check=check).stdout.strip()


def gh(*args, check=True) -> str:
    return run(["gh", *args], check=check).stdout.strip()


# ---- 公開前の確認 ------------------------------------------------------------

def checkTools() -> None:
    for tool in ("git", "gh", "npx"):
        if shutil.which(tool) is None:
            raise ReleaseError(f"{tool} が見つかりません")
    if run(["gh", "auth", "status"], check=False).returncode != 0:
        raise ReleaseError("GitHub CLI にログインしていません (gh auth login)")


def checkRepositoryState() -> None:
    branch = git("branch", "--show-current")
    if branch != RELEASE_BRANCH:
        raise ReleaseError(f"{RELEASE_BRANCH} ブランチで実行してください (今は {branch or '(ブランチ無し)'})")
    changed = git("status", "--porcelain", "--untracked-files=no")
    if changed:
        raise ReleaseError("コミットしていない変更があります。コミットするか退避してください:\n" + changed)
    git("fetch", "--quiet", "--tags", REMOTE)
    behind = int(git("rev-list", "--count", f"HEAD..{REMOTE}/{RELEASE_BRANCH}"))
    if behind:
        raise ReleaseError(f"{REMOTE}/{RELEASE_BRANCH} より {behind} コミット遅れています。pull してください")


def latestReleaseVersion():
    """公開済みのリリースで一番新しい版 (無ければ None)。"""
    listed = json.loads(gh("release", "list", "-R", REPO, "--limit", "100", "--json", "tagName") or "[]")
    versions = [item["tagName"] for item in listed if parseVersion(item["tagName"])]
    return max(versions, key=parseVersion) if versions else None


def checkVersionIsNew(version: str) -> None:
    tag = f"v{version}"
    if git("tag", "--list", tag):
        raise ReleaseError(f"タグ {tag} が手元にもうあります")
    if git("ls-remote", "--tags", REMOTE, f"refs/tags/{tag}"):
        raise ReleaseError(f"タグ {tag} が {REMOTE} にもうあります")
    latest = latestReleaseVersion()
    if latest and not isNewer(version, latest):
        raise ReleaseError(f"{version} は公開済みの {latest.lstrip('v')} より新しくありません")


def runTests() -> None:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise ReleaseError(".venv が見つかりません (npm run setup-python)")
    print("テスト: Python (pytest)")
    result = subprocess.run([str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT / "src-python")
    if result.returncode != 0:
        raise ReleaseError("Python のテストが失敗しました")
    print("テスト: UI のビルド (vite build)")
    # Windows の npx は npx.cmd なので、場所を解決してから呼ぶ。
    run([shutil.which("npx"), "vite", "build"], capture=True)


# ---- 版を書いてコミット・タグ・push ------------------------------------------

def writeVersion(version: str) -> None:
    update_version.setPackageVersion(version)
    update_version.update_versions()
    path = ROOT / BUILD_CHANNEL_FILE
    path.write_text(buildChannelContent(path.read_text(encoding="utf-8"), channelOf(version)),
                    encoding="utf-8", newline="\n")


def commitAndTag(version: str, trailers) -> None:
    git("add", *VERSION_FILES)
    if git("diff", "--cached", "--name-only"):
        args = ["commit", "-q", "-m", f"chore(release): v{version}"]
        for trailer in trailers:
            args += ["--trailer", trailer]
        git(*args)
    else:
        print("版はもう書いてあるので、リリースのコミットは作りません")
    git("tag", "-a", f"v{version}", "-m", f"VRCT-0 {version}")


def push(version: str) -> None:
    # ブランチとタグを一度に送る (片方だけ届いて CI が古いコミットでビルドするのを防ぐ)。
    git("push", "--atomic", REMOTE, RELEASE_BRANCH, f"v{version}")


# ---- CI と公開の確認 ---------------------------------------------------------

def listRunIds(tag: str) -> set:
    """そのタグで動いた CI の実行の id (push の前に控えておき、前回の実行と取り違えないため)。"""
    runs = json.loads(gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", "20",
                         "--json", "databaseId,headBranch") or "[]")
    return {item["databaseId"] for item in runs if item["headBranch"] == tag}


def findRun(tag: str, known_ids=frozenset(), timeout_sec: int = 120):
    """push で始まった CI の実行を探す。同じタグで前に動いた実行 (known_ids) は見ない。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        runs = json.loads(gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", "10",
                             "--json", "databaseId,headBranch,url") or "[]")
        for item in runs:
            if item["headBranch"] == tag and item["databaseId"] not in known_ids:
                return item
        time.sleep(5)
    raise ReleaseError(f"{tag} の CI が見つかりません。https://github.com/{REPO}/actions を確かめてください")


def watchRun(tag: str, known_ids=frozenset()) -> None:
    item = findRun(tag, known_ids)
    print(f"CI: {item['url']}")
    result = subprocess.run(["gh", "run", "watch", str(item["databaseId"]), "-R", REPO, "--exit-status",
                             "--interval", "30"], cwd=ROOT)
    if result.returncode != 0:
        raise ReleaseError(f"CI が失敗しました: {item['url']}")


def missingAssets(asset_names) -> list:
    return [pattern.pattern for pattern in REQUIRED_ASSET_PATTERNS
            if not any(pattern.match(name) for name in asset_names)]


def checkPublished(version: str) -> str:
    release = json.loads(gh("release", "view", f"v{version}", "-R", REPO,
                            "--json", "url,isPrerelease,isDraft,assets"))
    missing = missingAssets([asset["name"] for asset in release["assets"]])
    if missing:
        raise ReleaseError(f"リリースに必要なファイルがありません: {', '.join(missing)}")
    if release["isDraft"]:
        raise ReleaseError("リリースが下書きのままです")
    if release["isPrerelease"] != (channelOf(version) == "beta"):
        raise ReleaseError(f"プレリリースの印が版と合っていません (isPrerelease={release['isPrerelease']})")
    return release["url"]


# ---- 全体 --------------------------------------------------------------------

def plan(version: str) -> list:
    channel = channelOf(version)
    return [
        f"版を {version} にする ({', '.join(VERSION_FILES[:-1])})",
        f"BUILD_CHANNEL を \"{channel}\" にする",
        f"コミット chore(release): v{version} と、タグ v{version} を作る",
        f"{REMOTE} に {RELEASE_BRANCH} とタグを push する (CI が{'プレリリース' if channel == 'beta' else '正式版'}として公開する)",
        "CI の完了を待ち、公開されたファイルを確かめる",
    ]


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv=None) -> int:
    # パイプ越し (ほかのツールから動かすとき) でも日本語が化けないよう UTF-8 で出す。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="VRCT-0 を公開する (版を決めてタグを push し、CI の公開を見届ける)")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="版にする日付 (省くと今日)")
    parser.add_argument("--beta", type=int, metavar="N", help="ベータ版の番号")
    parser.add_argument("--dry-run", action="store_true", help="何をするかだけ表示する")
    parser.add_argument("--skip-tests", action="store_true", help="公開前のテストを省く")
    parser.add_argument("--yes", action="store_true", help="push の前の確認を省く")
    parser.add_argument("--no-watch", action="store_true", help="CI の完了を待たない")
    parser.add_argument("--trailer", action="append", default=[], help="リリースのコミットの末尾に付ける行")
    args = parser.parse_args(argv)

    if args.beta is not None and args.beta < 1:
        parser.error("--beta は 1 以上にしてください")
    date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    version = update_version.dateVersion(date, args.beta)

    try:
        checkTools()
        checkRepositoryState()
        checkVersionIsNew(version)

        print(f"VRCT-0 {version} を{'ベータ版' if args.beta else '正式版'}として公開します:")
        for step in plan(version):
            print(f"  - {step}")
        if args.dry_run:
            print("(--dry-run なので、何も変えずに終わります)")
            return 0

        if not args.skip_tests:
            runTests()
        writeVersion(version)
        commitAndTag(version, args.trailer)

        if not args.yes and not confirm(f"{REMOTE} に push して v{version} を公開しますか？"):
            print(f"push しませんでした。取り消すには: git tag -d v{version} (リリースのコミットを作った場合は、さらに git reset --hard HEAD~1)")
            return 1
        # やり直しのときは同じタグの前の実行が残っているので、push の前に控えておく。
        known_ids = listRunIds(f"v{version}")
        push(version)
        print(f"push しました: v{version}")

        if args.no_watch:
            print(f"CI: https://github.com/{REPO}/actions")
            return 0
        watchRun(f"v{version}", known_ids)
        url = checkPublished(version)
        print(f"公開しました: {url}")
        return 0
    except ReleaseError as error:
        print(f"中止しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
