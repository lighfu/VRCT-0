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
