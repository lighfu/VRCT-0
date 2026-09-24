import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import utils


class CudaLibraryDirsTests(unittest.TestCase):
    def test_external_dir_is_used_when_it_exists(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            external = os.path.join(data_dir, "cuda", "bin")
            os.makedirs(external)
            with patch.dict(os.environ, {"VRCT_DATA_DIR": data_dir}), \
                 patch.dict(sys.modules, {"nvidia": None}):
                self.assertEqual(utils.externalCudaLibraryDir(), external)
                self.assertEqual(utils._cudaLibraryDirs(), [external])

    def test_external_dir_is_ignored_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {"VRCT_DATA_DIR": data_dir}), \
                 patch.dict(sys.modules, {"nvidia": None}):
                self.assertIsNone(utils.externalCudaLibraryDir())
                self.assertEqual(utils._cudaLibraryDirs(), [])

    def test_bundled_dirs_come_before_external(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bundled = os.path.join(root, "nvidia", "cublas", "bin")
            os.makedirs(bundled)
            data_dir = os.path.join(root, "data")
            external = os.path.join(data_dir, "cuda", "bin")
            os.makedirs(external)
            fake_nvidia = types.ModuleType("nvidia")
            fake_nvidia.__path__ = [os.path.join(root, "nvidia")]
            with patch.dict(os.environ, {"VRCT_DATA_DIR": data_dir}), \
                 patch.dict(sys.modules, {"nvidia": fake_nvidia}):
                self.assertEqual(utils._cudaLibraryDirs(), [bundled, external])

    def test_original_vrct_folder_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as local_app_data:
            os.makedirs(os.path.join(local_app_data, "VRCT", "cuda", "bin"))
            env = {k: v for k, v in os.environ.items() if k != "VRCT_DATA_DIR"}
            env["LOCALAPPDATA"] = local_app_data
            with patch.dict(os.environ, env, clear=True):
                self.assertIsNone(utils.externalCudaLibraryDir())

    @unittest.skipUnless(os.name == "nt", "DLL search path registration is Windows-only")
    def test_register_adds_dll_directories_and_path(self) -> None:
        with patch.object(utils, "_cudaLibraryDirs", return_value=[r"C:\cuda\bin"]), \
             patch.object(utils.os, "add_dll_directory") as mock_add, \
             patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            utils._registerCudaLibraries()
            self.assertTrue(os.environ["PATH"].startswith(r"C:\cuda\bin" + os.pathsep))
        mock_add.assert_called_once_with(r"C:\cuda\bin")


if __name__ == "__main__":
    unittest.main()
