"""読みがな用 Sudachi の full 辞書の取得と確認。

標準は同梱の core 辞書 (SudachiDict-core)。full 辞書は 127MB の zip で
展開後 360MB あるため同梱せず、設定で選んだ人だけここで取得する
(A-1, 2026-09-23)。配布元は SudachiDict-full の setup.py と同じ CloudFront。
取得した zip は SHA256 を照合してから system_full.dic だけを取り出す。
"""

import hashlib
import os
import zipfile
from os import path as os_path
from time import sleep
from typing import Callable, Optional

from requests import get as requests_get

try:
    from utils import errorLogging, isWeightVerifiedCache, printLog, writeWeightVerifiedCache
except ImportError:
    import sys
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from utils import errorLogging, isWeightVerifiedCache, printLog, writeWeightVerifiedCache

SUDACHI_DICT_TYPES = ["core", "full"]
SUDACHI_FULL_DICT_URL = "https://d2ej7fkh96fzlu.cloudfront.net/sudachidict/sudachi-dictionary-20250825-full.zip"
SUDACHI_FULL_DICT_SHA256 = "7ce16d45e9ff0ccb70a3c8ba70346240ef246e6ad499cfab033fc2c1da4d19b4"
SUDACHI_FULL_DICT_MEMBER = "sudachi-dictionary-20250825/system_full.dic"

_DOWNLOAD_TIMEOUT = (10, 60)  # (connect, read) 秒
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF = 2  # 秒。attempt 番号を掛けて待機


def _fullDictDir(root: str) -> str:
    return os_path.join(root, "weights", "sudachi", "full")


def sudachiFullDictPath(root: str) -> str:
    return os_path.join(_fullDictDir(root), "system_full.dic")


def checkSudachiFullDict(root: str) -> bool:
    return os_path.isfile(sudachiFullDictPath(root)) and isWeightVerifiedCache(_fullDictDir(root))


def _removeQuietly(path: str) -> None:
    try:
        if os_path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _downloadArchive(zip_path: str, callback: Optional[Callable[[float], None]]) -> None:
    response = requests_get(SUDACHI_FULL_DICT_URL, stream=True, timeout=_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    file_size = int(response.headers.get("content-length", 0))
    digest = hashlib.sha256()
    received = 0
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 2000):
            f.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            if callback is not None and file_size > 0:
                callback(received / file_size)
    if digest.hexdigest() != SUDACHI_FULL_DICT_SHA256:
        raise ValueError("Sudachi full dictionary checksum mismatch")


def _extractDictionary(zip_path: str, dict_path: str) -> None:
    temporary_path = dict_path + ".tmp"
    with zipfile.ZipFile(zip_path) as archive, archive.open(SUDACHI_FULL_DICT_MEMBER) as source, \
            open(temporary_path, "wb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
    os.replace(temporary_path, dict_path)


def downloadSudachiFullDict(
    root: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> bool:
    if checkSudachiFullDict(root):
        return True
    directory = _fullDictDir(root)
    os.makedirs(directory, exist_ok=True)
    zip_path = os_path.join(directory, "sudachi-dictionary-full.zip")
    dict_path = sudachiFullDictPath(root)
    succeeded = False
    try:
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                _downloadArchive(zip_path, callback)
                _extractDictionary(zip_path, dict_path)
                _removeQuietly(zip_path)
                writeWeightVerifiedCache(directory)
                succeeded = True
                break
            except Exception:
                errorLogging()
                _removeQuietly(dict_path + ".tmp")
                _removeQuietly(dict_path)
                _removeQuietly(zip_path)
                if attempt < _DOWNLOAD_MAX_ATTEMPTS:
                    printLog(f"Sudachi full dictionary download failed, retrying ({attempt}/{_DOWNLOAD_MAX_ATTEMPTS - 1})")
                    sleep(_DOWNLOAD_RETRY_BACKOFF * attempt)
    finally:
        if end_callback is not None:
            end_callback()
    return succeeded
