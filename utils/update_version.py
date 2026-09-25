"""VRCT-0 の版を決めて、版を持つファイルにそろえる。

版はリリースした日付 (年.月.日) にする。ベータ版は後ろに -beta.<番号> を付ける。
    例: 2026.9.25 / 2026.9.25-beta.1
更新の仕組み (Velopack) は SemVer の版しか受け付けないので、月と日はゼロで埋めない
(2026.09.25 は使えない)。数字は 3 つまでなので、同じ日に 2 回目の正式版は出せない
(続けて直すときはベータ版で出すか、翌日の日付で出す)。

使い方:
    python utils/update_version.py                         package.json の版をほかのファイルへ写す
    python utils/update_version.py --date                  今日の日付を版にする
    python utils/update_version.py --date 2026-09-25 --beta 1
"""

import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATE_VERSION_RE = re.compile(r"^(\d{4})\.([1-9]\d?)\.([1-9]\d?)(?:-(?:beta|rc)\.[1-9]\d*)?$")


def dateVersion(date: datetime.date, beta: int = None) -> str:
    version = f"{date.year}.{date.month}.{date.day}"
    return f"{version}-beta.{beta}" if beta else version


def isDateVersion(version: str) -> bool:
    """年.月.日 (ベータ版は -beta.N / -rc.N 付き) で、実在する日付なら True。"""
    match = DATE_VERSION_RE.match(version)
    if match is None:
        return False
    try:
        datetime.date(*(int(part) for part in match.groups()))
    except ValueError:
        return False
    return True


def _path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _replaceOnce(content: str, pattern: str, version: str, label: str) -> str:
    new_content, count = re.subn(pattern, rf"\g<1>{version}\g<2>", content, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError(f"{label} に版の行が見つからない")
    return new_content


def setPackageVersion(version: str) -> None:
    """package.json と package-lock.json の版を書き換える (ほかの行はそのまま)。"""
    package_path = _path("package.json")
    _write(package_path, _replaceOnce(_read(package_path), r'^(  "version": ")[^"]+(")', version, "package.json"))

    lock_path = _path("package-lock.json")
    if os.path.isfile(lock_path):
        content = _replaceOnce(_read(lock_path), r'^(  "version": ")[^"]+(")', version, "package-lock.json")
        content = _replaceOnce(content, r'("": \{\s*"name": "[^"]*",\s*"version": ")[^"]+(")', version, "package-lock.json")
        _write(lock_path, content)


def update_versions() -> str:
    """package.json の版を tauri.conf.json と config.py へ写す。"""
    with open(_path("package.json"), "r", encoding="utf-8") as f:
        version = json.load(f)["version"]
    if not isDateVersion(version):
        raise SystemExit(
            f"package.json の版 {version} が日付の形 (例: 2026.9.25 / 2026.9.25-beta.1) ではありません。"
            " npm run set-version で直してください。"
        )

    tauri_conf_path = _path("src-tauri", "tauri.conf.json")
    with open(tauri_conf_path, "r", encoding="utf-8") as f:
        tauri_conf = json.load(f)
    tauri_conf["version"] = version
    with open(tauri_conf_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(tauri_conf, f, indent=4, ensure_ascii=False)

    config_path = _path("src-python", "config.py")
    _write(config_path, _replaceOnce(_read(config_path), r'(self\._VERSION = ")[^"]+(")', version, "config.py"))

    print(f"updated to version {version}")
    return version


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="VRCT-0 の版 (リリースした日付) を決めて、各ファイルにそろえる")
    parser.add_argument("--date", nargs="?", const="today", metavar="YYYY-MM-DD",
                        help="この日付を版にする (日付を省くと今日)")
    parser.add_argument("--beta", type=int, metavar="N", help="ベータ版の番号 (--date と一緒に使う)")
    args = parser.parse_args(argv)

    if args.beta is not None and args.date is None:
        parser.error("--beta は --date と一緒に使ってください")
    if args.beta is not None and args.beta < 1:
        parser.error("--beta は 1 以上にしてください")

    if args.date is not None:
        date = datetime.date.today() if args.date == "today" else datetime.date.fromisoformat(args.date)
        setPackageVersion(dateVersion(date, args.beta))
    update_versions()


if __name__ == "__main__":
    main(sys.argv[1:])
