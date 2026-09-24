"""GPU 高速化パックの状態の判定と、取得・検証・展開・削除の予約のテスト。

wheel は小さな偽物 (zip) を作り、cuda_pack.WHEELS と cuda_pack.requests_get を差し替える。
"""

import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

import utils
from models import cuda_pack


def _wheel_bytes(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _response(data: bytes, chunks=None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.headers = {"content-length": str(len(data))}
    response.iter_content.return_value = chunks if chunks is not None else [data[:10], data[10:]]
    response.raise_for_status.return_value = None
    return response


CUBLAS = _wheel_bytes({
    "nvidia/cublas/bin/cublas64_12.dll": b"a" * 100,
    "nvidia/cublas/include/cublas.h": b"header",
})
CUDNN = _wheel_bytes({
    "nvidia/cudnn/bin/cudnn64_9.dll": b"b" * 50,
    "nvidia/cudnn/bin/cudnn_ops64_9.dll": b"c" * 50,
})


def _wheels(cudnn_sha=None):
    return (
        cuda_pack.Wheel("cublas", "https://example.invalid/cublas.whl", len(CUBLAS), hashlib.sha256(CUBLAS).hexdigest()),
        cuda_pack.Wheel("cudnn", "https://example.invalid/cudnn.whl", len(CUDNN), cudnn_sha or hashlib.sha256(CUDNN).hexdigest()),
    )


def _fake_get(url, **kwargs):
    return _response(CUBLAS if url.endswith("cublas.whl") else CUDNN)


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = os.path.join(tmp.name, "cuda")
        for target, value in (("sleep", MagicMock()), ("REQUIRED_FREE_BYTES", 1)):
            patcher = patch.object(cuda_pack, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _download(self, wheels, get=_fake_get):
        progress = []
        end = MagicMock()
        with patch.object(cuda_pack, "WHEELS", wheels), \
                patch.object(cuda_pack, "requests_get", side_effect=get) as mock_get:
            ok = cuda_pack.downloadCudaPack(progress.append, end, directory=self.directory)
        return ok, progress, end, mock_get

    def _install_old_pack(self):
        old_bin = os.path.join(self.directory, "bin")
        os.makedirs(old_bin)
        open(os.path.join(old_bin, "old.dll"), "w").close()
        with open(os.path.join(self.directory, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"pack_id": "cu11-old", "files": ["old.dll"]}, f)
        return old_bin

    def test_download_installs_dlls_and_manifest(self) -> None:
        ok, progress, end, _ = self._download(_wheels())
        self.assertTrue(ok)
        end.assert_called_once_with(cuda_pack.RESULT_INSTALLED)
        bin_dir = os.path.join(self.directory, "bin")
        self.assertEqual(sorted(os.listdir(bin_dir)), ["cublas64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll"])
        with open(os.path.join(self.directory, "pack.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["pack_id"], utils.CUDA_PACK_ID)
        self.assertEqual(manifest["files"], ["cublas64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll"])
        self.assertFalse(os.path.exists(os.path.join(self.directory, "download")))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "bin.tmp")))
        self.assertEqual(progress, sorted(progress))
        self.assertAlmostEqual(progress[-1], 1.0)
        end.assert_called_once()
        self.assertTrue(cuda_pack.isInstalled(self.directory))

    def test_checksum_mismatch_leaves_nothing(self) -> None:
        ok, _, end, mock_get = self._download(_wheels(cudnn_sha="0" * 64))
        self.assertFalse(ok)
        end.assert_called_once_with(cuda_pack.RESULT_FAILED)
        self.assertFalse(os.path.exists(os.path.join(self.directory, "bin")))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "pack.json")))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "download")))
        end.assert_called_once()
        # cublas は 1 回で通り、cudnn は 3 回まで試す。
        self.assertEqual(mock_get.call_count, 1 + cuda_pack._DOWNLOAD_MAX_ATTEMPTS)

    def test_failure_keeps_an_existing_installation(self) -> None:
        old_bin = os.path.join(self.directory, "bin")
        os.makedirs(old_bin)
        open(os.path.join(old_bin, "old.dll"), "w").close()
        with open(os.path.join(self.directory, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"pack_id": "cu11-old", "files": ["old.dll"]}, f)

        def fail_on_cudnn(url, **kwargs):
            if url.endswith("cudnn.whl"):
                raise ConnectionError("network down")
            return _fake_get(url)

        ok, _, _, _ = self._download(_wheels(), get=fail_on_cudnn)
        self.assertFalse(ok)
        self.assertEqual(os.listdir(old_bin), ["old.dll"])
        self.assertEqual(utils.installedCudaPackId(self.directory), "cu11-old")

    def test_bin_in_use_stops_before_downloading(self) -> None:
        old_bin = self._install_old_pack()
        with patch.object(cuda_pack, "_binInUse", return_value=True):
            ok, _, end, mock_get = self._download(_wheels())
        self.assertFalse(ok)
        mock_get.assert_not_called()
        end.assert_called_once_with(cuda_pack.RESULT_IN_USE)
        self.assertEqual(os.listdir(old_bin), ["old.dll"])
        self.assertEqual(utils.installedCudaPackId(self.directory), "cu11-old")
        self.assertFalse(os.path.exists(os.path.join(self.directory, "download")))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "bin.tmp")))

    def test_bin_taken_during_the_download_is_left_alone(self) -> None:
        # 落としているあいだに、ほかのプロセスが古い bin の DLL を読み込んだ場合。
        old_bin = self._install_old_pack()
        with patch.object(cuda_pack, "_binInUse", side_effect=[False, True]):
            ok, _, end, mock_get = self._download(_wheels())
        self.assertFalse(ok)
        self.assertEqual(mock_get.call_count, 2)
        end.assert_called_once_with(cuda_pack.RESULT_IN_USE)
        self.assertEqual(os.listdir(old_bin), ["old.dll"])
        self.assertEqual(utils.installedCudaPackId(self.directory), "cu11-old")
        self.assertFalse(os.path.exists(os.path.join(self.directory, "download")))
        self.assertFalse(os.path.exists(os.path.join(self.directory, "bin.tmp")))

    def test_bin_that_fails_to_delete_at_the_swap_is_reported_as_in_use(self) -> None:
        self._install_old_pack()
        real_rmtree = cuda_pack.shutil.rmtree
        final_bin = os.path.join(self.directory, "bin")

        def rmtree(path, *args, **kwargs):
            if os.path.normcase(path) == os.path.normcase(final_bin) and not kwargs.get("ignore_errors"):
                raise PermissionError(5, "Access is denied", path)
            return real_rmtree(path, *args, **kwargs)

        with patch.object(cuda_pack.shutil, "rmtree", side_effect=rmtree):
            ok, _, end, _ = self._download(_wheels())
        self.assertFalse(ok)
        end.assert_called_once_with(cuda_pack.RESULT_IN_USE)

    def test_not_enough_disk_space_does_not_start(self) -> None:
        usage = MagicMock(free=10)
        with patch.object(cuda_pack, "REQUIRED_FREE_BYTES", 1000), \
                patch.object(cuda_pack.shutil, "disk_usage", return_value=usage):
            ok, _, end, mock_get = self._download(_wheels())
        self.assertFalse(ok)
        mock_get.assert_not_called()
        end.assert_called_once()

    def test_wheel_without_dlls_fails(self) -> None:
        empty = _wheel_bytes({"nvidia/cudnn/include/x.h": b"h"})
        wheels = (
            _wheels()[0],
            cuda_pack.Wheel("cudnn", "https://example.invalid/cudnn.whl", len(empty), hashlib.sha256(empty).hexdigest()),
        )

        def get(url, **kwargs):
            return _response(CUBLAS if url.endswith("cublas.whl") else empty)

        ok, _, _, _ = self._download(wheels, get=get)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(os.path.join(self.directory, "bin")))


class BinInUseTests(unittest.TestCase):
    """_binInUse: 古い bin の DLL を、ほかのプロセスが読み込んだままか。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin_dir = os.path.join(tmp.name, "cuda", "bin")
        patcher = patch.object(cuda_pack, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_bin_is_not_in_use(self) -> None:
        self.assertFalse(cuda_pack._binInUse(self.bin_dir))

    def test_idle_bin_is_not_in_use_and_is_left_unchanged(self) -> None:
        os.makedirs(self.bin_dir)
        path = os.path.join(self.bin_dir, "cublas64_12.dll")
        with open(path, "wb") as f:
            f.write(b"dll")
        before = os.stat(path)
        self.assertFalse(cuda_pack._binInUse(self.bin_dir))
        after = os.stat(path)
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"dll")

    @unittest.skipUnless(os.name == "nt", "DLL loading is Windows-only")
    def test_dll_loaded_by_another_process_is_in_use(self) -> None:
        # 読み込まれた DLL は、フォルダごと名前を変えられるが消せない (2026-09-24 に確かめた)。
        # 前のサイドカーが残っている状態を、DLL を読み込んだ別のプロセスで作る。
        source = next(iter(glob.glob(os.path.join(sys.base_prefix, "DLLs", "*.dll"))), None)
        if source is None:
            self.skipTest("no DLL to copy in this Python")
        os.makedirs(self.bin_dir)
        dll = os.path.join(self.bin_dir, "cublas64_12.dll")
        shutil.copyfile(source, dll)
        holder = subprocess.Popen(
            [sys.executable, "-c", f"import ctypes, sys; ctypes.WinDLL({dll!r}); print('loaded', flush=True); sys.stdin.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "loaded")
            self.assertTrue(cuda_pack._binInUse(self.bin_dir))
        finally:
            holder.stdin.close()
            holder.wait(timeout=10)
            holder.stdout.close()
        self.assertFalse(cuda_pack._binInUse(self.bin_dir))


class DownloadWheelTests(unittest.TestCase):
    """1 つの wheel の取得 (_downloadWheel)。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "cublas.whl")
        self.wheel = _wheels()[0]

    def test_the_response_is_closed_after_a_successful_download(self) -> None:
        response = _response(CUBLAS)
        with patch.object(cuda_pack, "requests_get", return_value=response):
            cuda_pack._downloadWheel(self.wheel, self.path, lambda received: None)
        response.__exit__.assert_called_once()
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), CUBLAS)

    def test_the_response_is_closed_after_a_failed_download(self) -> None:
        response = _response(CUBLAS)
        response.iter_content.side_effect = ConnectionError("reset")
        with patch.object(cuda_pack, "requests_get", return_value=response):
            with self.assertRaises(ConnectionError):
                cuda_pack._downloadWheel(self.wheel, self.path, lambda received: None)
        response.__exit__.assert_called_once()

    def test_more_bytes_than_the_pinned_size_stop_the_download(self) -> None:
        # 送り続けるサーバーで、ハッシュを見る前にディスクを埋めないように。
        endless = [CUBLAS] + [b"x" * 1000] * 1000
        response = _response(CUBLAS, chunks=endless)
        received = []
        with patch.object(cuda_pack, "requests_get", return_value=response):
            with self.assertRaises(ValueError):
                cuda_pack._downloadWheel(self.wheel, self.path, received.append)
        response.__exit__.assert_called_once()
        self.assertLessEqual(os.path.getsize(self.path), self.wheel.size)
        self.assertLessEqual(max(received), self.wheel.size)


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = os.path.join(tmp.name, "cuda")

    def _status(self, devices=1, driver=12080, loaded=False, downloading=False):
        with patch.object(cuda_pack, "_cudaDeviceCount", return_value=devices), \
                patch.object(cuda_pack, "cudaDriverVersion", return_value=driver), \
                patch.object(cuda_pack, "cudaPackLoaded", return_value=loaded):
            return cuda_pack.status(downloading=downloading, directory=self.directory)

    def _install(self, pack_id=utils.CUDA_PACK_ID):
        os.makedirs(os.path.join(self.directory, "bin"))
        with open(os.path.join(self.directory, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"pack_id": pack_id, "files": []}, f)

    def test_no_gpu(self) -> None:
        self.assertEqual(self._status(devices=0), "no_gpu")

    def test_driver_too_old(self) -> None:
        self.assertEqual(self._status(driver=11080), "driver_too_old")

    def test_not_installed(self) -> None:
        self.assertEqual(self._status(), "not_installed")

    def test_other_pack_version_is_not_installed(self) -> None:
        self._install(pack_id="cu11-old")
        self.assertEqual(self._status(), "not_installed")

    def test_installed_but_not_loaded(self) -> None:
        self._install()
        self.assertEqual(self._status(loaded=False), "installed_restart_required")

    def test_installed_and_loaded(self) -> None:
        self._install()
        self.assertEqual(self._status(loaded=True), "installed")

    def test_downloading(self) -> None:
        self.assertEqual(self._status(downloading=True), "downloading")

    def test_remove_pending(self) -> None:
        self._install()
        cuda_pack.requestRemoval(self.directory)
        self.assertTrue(os.path.isfile(os.path.join(self.directory, utils.CUDA_PACK_REMOVE_MARKER)))
        self.assertEqual(self._status(loaded=True), "remove_pending")

    def test_device_count_failure_means_no_gpu(self) -> None:
        with patch.object(cuda_pack, "_ct2_get_cuda_device_count", side_effect=RuntimeError("boom")):
            self.assertEqual(cuda_pack._cudaDeviceCount(), 0)


if __name__ == "__main__":
    unittest.main()
