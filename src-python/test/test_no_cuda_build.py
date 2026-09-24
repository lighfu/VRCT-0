"""CUDA 版のビルドをやめたこと (GPU はアプリから導入する GPU 高速化パックで使う) を確かめる。"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class NoCudaBuildTests(unittest.TestCase):
    def test_cuda_build_files_are_gone(self) -> None:
        for relative in ("spec/backend_cuda.spec", "bat/build_cuda.bat", "requirements_cuda.txt"):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_package_json_has_no_cuda_scripts(self) -> None:
        scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
        self.assertEqual([name for name in scripts if "cuda" in name], [])
        self.assertEqual([name for name, command in scripts.items() if ".venv_cuda" in command], [])

    def test_install_bat_does_not_create_the_cuda_venv(self) -> None:
        self.assertNotIn(".venv_cuda", (ROOT / "bat" / "install.bat").read_text(encoding="utf-8"))

    def test_dev_sidecar_does_not_offer_the_cuda_venv(self) -> None:
        source = (ROOT / "utils" / "dev_sidecar" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotIn(".venv_cuda", source)

    def test_nothing_points_at_the_removed_cuda_requirements(self) -> None:
        for relative in ("requirements.txt", "requirements-dev.txt", "requirements-yolo-train.txt"):
            path = ROOT / relative
            if path.exists():
                self.assertNotIn("requirements_cuda.txt", path.read_text(encoding="utf-8"), relative)
