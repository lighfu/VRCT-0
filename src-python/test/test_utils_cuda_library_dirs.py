"""GPU 部品 (<データの置き場所>\\cuda) の置き場所・版の印・削除の予約・DLL の登録のテスト。"""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import utils


def _install_pack(data_dir: str, pack_id: str = utils.CUDA_PACK_ID) -> str:
    """data_dir\\cuda\\bin と pack.json を作り、bin のパスを返す。"""
    directory = os.path.join(data_dir, "cuda")
    bin_dir = os.path.join(directory, "bin")
    os.makedirs(bin_dir)
    with open(os.path.join(directory, utils.CUDA_PACK_MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"pack_id": pack_id, "files": []}, f)
    return bin_dir


def _env_with_data_dir(data_dir):
    env = {k: v for k, v in os.environ.items() if k != utils.DATA_DIR_ENV}
    if data_dir is not None:
        env[utils.DATA_DIR_ENV] = data_dir
    return env


class CudaPackDirectoryTests(unittest.TestCase):
    def test_installed_layout_uses_the_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True):
                self.assertEqual(utils.cudaPackDirectory(), os.path.join(data_dir, "cuda"))

    def test_dev_layout_uses_the_app_dir(self) -> None:
        with tempfile.TemporaryDirectory() as app_dir:
            with patch.dict(os.environ, _env_with_data_dir(None), clear=True), \
                    patch.object(utils, "_appDirectory", return_value=app_dir):
                self.assertEqual(utils.cudaPackDirectory(), os.path.join(app_dir, "cuda"))


class ExternalCudaLibraryDirTests(unittest.TestCase):
    def test_matching_pack_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            bin_dir = _install_pack(data_dir)
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True), \
                    patch.dict(sys.modules, {"nvidia": None}):
                self.assertEqual(utils.externalCudaLibraryDir(), bin_dir)
                self.assertEqual(utils._cudaLibraryDirs(), [bin_dir])

    def test_mismatched_pack_is_not_registered(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            _install_pack(data_dir, pack_id="cu11-old")
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True), \
                    patch.dict(sys.modules, {"nvidia": None}):
                self.assertIsNone(utils.externalCudaLibraryDir())
                self.assertEqual(utils._cudaLibraryDirs(), [])

    def test_bin_without_manifest_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            os.makedirs(os.path.join(data_dir, "cuda", "bin"))
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True):
                self.assertIsNone(utils.externalCudaLibraryDir())

    def test_bundled_dirs_come_before_external(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bundled = os.path.join(root, "nvidia", "cublas", "bin")
            os.makedirs(bundled)
            data_dir = os.path.join(root, "data")
            external = _install_pack(data_dir)
            fake_nvidia = types.ModuleType("nvidia")
            fake_nvidia.__path__ = [os.path.join(root, "nvidia")]
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True), \
                    patch.dict(sys.modules, {"nvidia": fake_nvidia}):
                self.assertEqual(utils._cudaLibraryDirs(), [bundled, external])

    def test_original_vrct_folder_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as local_app_data, tempfile.TemporaryDirectory() as app_dir:
            os.makedirs(os.path.join(local_app_data, "VRCT", "cuda", "bin"))
            env = _env_with_data_dir(None)
            env["LOCALAPPDATA"] = local_app_data
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(utils, "_appDirectory", return_value=app_dir):
                self.assertIsNone(utils.externalCudaLibraryDir())


class PendingRemovalTests(unittest.TestCase):
    def test_pending_removal_deletes_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            _install_pack(data_dir)
            marker = os.path.join(data_dir, "cuda", utils.CUDA_PACK_REMOVE_MARKER)
            open(marker, "w").close()
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True):
                self.assertTrue(utils.processPendingCudaPackRemoval())
                self.assertFalse(os.path.exists(os.path.join(data_dir, "cuda")))
                self.assertIsNone(utils.externalCudaLibraryDir())

    def test_no_marker_keeps_the_pack(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            bin_dir = _install_pack(data_dir)
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True):
                self.assertFalse(utils.processPendingCudaPackRemoval())
                self.assertTrue(os.path.isdir(bin_dir))


class RegisterTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "DLL search path registration is Windows-only")
    def test_register_adds_dll_directories_and_path(self) -> None:
        with patch.object(utils, "_cudaLibraryDirs", return_value=[r"C:\cuda\bin"]), \
                patch.object(utils, "externalCudaLibraryDir", return_value=None), \
                patch.object(utils.os, "add_dll_directory") as mock_add, \
                patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            utils._registerCudaLibraries()
            self.assertTrue(os.environ["PATH"].startswith(r"C:\cuda\bin" + os.pathsep))
        mock_add.assert_called_once_with(r"C:\cuda\bin")

    @unittest.skipUnless(os.name == "nt", "DLL search path registration is Windows-only")
    def test_loaded_flag_follows_the_external_dir(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            bin_dir = _install_pack(data_dir)
            with patch.dict(os.environ, _env_with_data_dir(data_dir), clear=True), \
                    patch.dict(sys.modules, {"nvidia": None}), \
                    patch.object(utils.os, "add_dll_directory"):
                utils._registerCudaLibraries()
                self.assertTrue(utils.cudaPackLoaded())
        with tempfile.TemporaryDirectory() as empty_dir:
            with patch.dict(os.environ, _env_with_data_dir(empty_dir), clear=True), \
                    patch.dict(sys.modules, {"nvidia": None}):
                utils._registerCudaLibraries()
                self.assertFalse(utils.cudaPackLoaded())


if __name__ == "__main__":
    unittest.main()
