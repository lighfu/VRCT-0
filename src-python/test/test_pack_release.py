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
