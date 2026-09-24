"""CUDA 版のビルドをやめたこと (GPU はアプリから導入する部品で使う) を確かめる。"""

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
