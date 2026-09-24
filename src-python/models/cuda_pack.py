"""GPU 部品 (CUDA の cuBLAS / cuDNN) の状態・取得・削除 (インストーラー サブプロジェクト 2)。

アプリは CPU 版だけを配り、NVIDIA の GPU がある人だけがここで部品を導入する。
部品は NVIDIA が PyPI に公開している公式の wheel から取り、版と SHA-256 は
WHEELS に固定する (版を変えるときは WHEELS と utils.CUDA_PACK_ID を一緒に変える)。
wheel の中の nvidia/*/bin/*.dll だけを <データの置き場所>\\cuda\\bin に展開する。
DLL は起動時に 1 回だけ読み込まれる (utils._registerCudaLibraries) ので、
導入・削除は再起動で反映される。
"""

import ctypes
import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from os import path as os_path
from time import sleep
from typing import Callable, List, Optional

from requests import get as requests_get

from utils import (
    CUDA_PACK_ID,
    CUDA_PACK_MANIFEST,
    CUDA_PACK_REMOVE_MARKER,
    _ct2_get_cuda_device_count,
    cudaPackDirectory,
    cudaPackLoaded,
    errorLogging,
    installedCudaPackId,
    printLog,
)


@dataclass(frozen=True)
class Wheel:
    name: str
    url: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


WHEELS = (
    Wheel(
        "nvidia-cublas-cu12 12.8.4.1",
        "https://files.pythonhosted.org/packages/70/61/7d7b3c70186fb651d0fbd35b01dbfc8e755f69fd58f817f3d0f642df20c3/nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl",
        567544208,
        "47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af",
    ),
    Wheel(
        "nvidia-cudnn-cu12 9.7.1.26",
        "https://files.pythonhosted.org/packages/d0/ea/636cda41b3865caa0d43c34f558167304acde3d2c5f6c54c00a550e69ecd/nvidia_cudnn_cu12-9.7.1.26-py3-none-win_amd64.whl",
        715962100,
        "7b805b9a4cf9f3da7c5f4ea4a9dff7baf62d1a612d6154a7e0d2ea51ed296241",
    ),
)

# wheel 2 つ (約 1.28 GB) + 展開後の DLL (約 1.77 GB) + 余裕。
REQUIRED_FREE_BYTES = 3_300_000_000
MIN_DRIVER_CUDA_VERSION = 12000

_DOWNLOAD_TIMEOUT = (10, 60)  # (connect, read) 秒
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF = 2  # 秒。attempt 番号を掛けて待つ


def _cudaDeviceCount() -> int:
    try:
        return int(_ct2_get_cuda_device_count())
    except Exception:
        return 0


def cudaDriverVersion() -> int:
    """ドライバーが対応する CUDA の版 (例: 12080)。取れなければ 0。"""
    try:
        cuda = ctypes.CDLL("nvcuda.dll" if os.name == "nt" else "libcuda.so.1")
        version = ctypes.c_int()
        if cuda.cuDriverGetVersion(ctypes.byref(version)) != 0:
            return 0
        return version.value
    except Exception:
        return 0


def isInstalled(directory: Optional[str] = None) -> bool:
    return installedCudaPackId(directory or cudaPackDirectory()) == CUDA_PACK_ID


def status(downloading: bool = False, directory: Optional[str] = None) -> str:
    directory = directory or cudaPackDirectory()
    if os_path.isfile(os_path.join(directory, CUDA_PACK_REMOVE_MARKER)):
        return "remove_pending"
    if downloading:
        return "downloading"
    if _cudaDeviceCount() <= 0:
        return "no_gpu"
    if cudaDriverVersion() < MIN_DRIVER_CUDA_VERSION:
        return "driver_too_old"
    if not isInstalled(directory):
        return "not_installed"
    return "installed" if cudaPackLoaded() else "installed_restart_required"


def requestRemoval(directory: Optional[str] = None) -> None:
    """削除を予約する。DLL は使用中で消せないので、次の起動で utils が消す。"""
    directory = directory or cudaPackDirectory()
    os.makedirs(directory, exist_ok=True)
    with open(os_path.join(directory, CUDA_PACK_REMOVE_MARKER), "w", encoding="utf-8") as f:
        f.write("1")


def _downloadWheel(wheel: Wheel, path: str, on_bytes: Callable[[int], None]) -> None:
    response = requests_get(wheel.url, stream=True, timeout=_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    digest = hashlib.sha256()
    received = 0
    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 2000):
            f.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            on_bytes(received)
    if digest.hexdigest() != wheel.sha256:
        raise ValueError(f"{wheel.name}: checksum mismatch")


def _downloadWheelWithRetry(wheel: Wheel, path: str, on_bytes: Callable[[int], None]) -> None:
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            _downloadWheel(wheel, path, on_bytes)
            return
        except Exception:
            errorLogging()
            if os_path.exists(path):
                os.remove(path)
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                raise
            printLog(f"GPU parts download failed, retrying ({attempt}/{_DOWNLOAD_MAX_ATTEMPTS - 1})")
            sleep(_DOWNLOAD_RETRY_BACKOFF * attempt)


def _extractDlls(wheel_path: str, target_dir: str) -> List[str]:
    """wheel の nvidia/<lib>/bin/*.dll だけを target_dir に取り出す (名前だけ使うのでフォルダの外へは出ない)。"""
    names: List[str] = []
    with zipfile.ZipFile(wheel_path) as archive:
        for member in archive.namelist():
            parts = member.split("/")
            if len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "bin" and parts[3].lower().endswith(".dll"):
                name = parts[3]
                with archive.open(member) as source, open(os_path.join(target_dir, name), "wb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                names.append(name)
    if not names:
        raise ValueError(f"{os_path.basename(wheel_path)}: no DLLs in the wheel")
    return names


def downloadCudaPack(
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
    directory: Optional[str] = None,
) -> bool:
    """GPU 部品を取得・検証・展開する。失敗しても前から入っていた部品は残す。"""
    directory = directory or cudaPackDirectory()
    download_dir = os_path.join(directory, "download")
    temporary_bin = os_path.join(directory, "bin.tmp")
    final_bin = os_path.join(directory, "bin")
    manifest_path = os_path.join(directory, CUDA_PACK_MANIFEST)
    succeeded = False
    try:
        os.makedirs(directory, exist_ok=True)
        if shutil.disk_usage(directory).free < REQUIRED_FREE_BYTES:
            printLog("GPU parts: not enough free disk space")
            return False
        shutil.rmtree(download_dir, ignore_errors=True)
        shutil.rmtree(temporary_bin, ignore_errors=True)
        os.makedirs(download_dir)
        os.makedirs(temporary_bin)

        total = sum(wheel.size for wheel in WHEELS)
        done = 0
        wheel_paths = []
        for wheel in WHEELS:
            path = os_path.join(download_dir, wheel.filename)
            base = done

            def on_bytes(received: int, base: int = base) -> None:
                if callback is not None and total > 0:
                    callback(min(1.0, (base + received) / total))

            _downloadWheelWithRetry(wheel, path, on_bytes)
            done += wheel.size
            wheel_paths.append(path)

        names: List[str] = []
        for path in wheel_paths:
            names.extend(_extractDlls(path, temporary_bin))

        # 置き換えは全部そろってから。pack.json は最後に書く (これがあれば導入済み)。
        if os_path.exists(manifest_path):
            os.remove(manifest_path)
        if os_path.exists(final_bin):
            shutil.rmtree(final_bin)
        os.replace(temporary_bin, final_bin)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"pack_id": CUDA_PACK_ID, "files": sorted(names)}, f)
        succeeded = True
    except Exception:
        errorLogging()
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        shutil.rmtree(temporary_bin, ignore_errors=True)
        if end_callback is not None:
            end_callback()
    return succeeded
