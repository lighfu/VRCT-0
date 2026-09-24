"""ローカル SenseVoice (sherpa-onnx) による文字起こし。

SenseVoice-Small は非自己回帰型のため、Whisper のように1トークンずつ
生成せず1回の推論で全文を出す。CPU でも音声の長さの数%の時間で終わり、
日本語の精度は Whisper large-v3-turbo と同程度 (Common Voice 日本語 60文、
4スレッド CPU で CER 16.1% / 処理時間は音声長の 0.04 倍。large-v3-turbo-int8
は CER 15.6% / 1.0 倍、base は CER 32.5% / 0.15 倍)。
対応言語は日本語・英語・中国語・広東語・韓国語。

モデルは Hugging Face から weights/sensevoice/ に取得する (int8 量子化版、
約 230MB)。2025-09-09 版は同じ評価で CER 79% と大きく劣化したため、
2024-07-17 版をコミットで固定して使う。
"""

import hashlib
import importlib.util
from os import path as os_path, makedirs as os_makedirs, remove as os_remove, replace as os_replace
from threading import Lock
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from models.transcription.transcription_whisper import downloadFile, transcriptionCpuThreads
from utils import errorLogging, isWeightVerifiedCache, writeWeightVerifiedCache

# UI のダウンロード欄 (weight_download_status) で使う重みの ID。重みは1種類だけ。
SENSEVOICE_WEIGHT_TYPE = "sensevoice-small"
SENSEVOICE_REPO_ID = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# main は差し替わりうるため、中身を確かめたコミットに固定する。
SENSEVOICE_REVISION = "2365baeacb507f821a0c8120fcee3d484dba7a07"
_MODEL_FILE = "model.int8.onnx"
_TOKENS_FILE = "tokens.txt"
# ファイル名 -> (大きさ, SHA-256)。取得後にこれと照合できたものだけを置く。
_FILES: Dict[str, Tuple[int, str]] = {
    _MODEL_FILE: (239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
    _TOKENS_FILE: (315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
}

sherpa_onnx = None  # 使うときに loadSenseVoiceRuntime() が読み込む (起動時間の短縮)


class SenseVoiceRuntimeError(RuntimeError):
    """sherpa_onnx を読み込めない (VC++ ランタイムが無い、DLL が読めない等)。"""


def isSenseVoiceRuntimeInstalled() -> bool:
    """sherpa_onnx が入っているか。import はしない (起動時に DLL を読み込まない)。"""
    return importlib.util.find_spec("sherpa_onnx") is not None


def loadSenseVoiceRuntime():
    """sherpa_onnx を読み込んで返す。読み込めなければ None
    (VC++ ランタイムが無い、DLL が読めない等)。"""
    global sherpa_onnx
    if sherpa_onnx is None:
        try:
            import sherpa_onnx as _module
        except Exception:
            errorLogging()
            return None
        sherpa_onnx = _module
    return sherpa_onnx


def _weightDir(root: str) -> str:
    return os_path.join(root, "weights", "sensevoice")


def _removeQuietly(file_path: str) -> None:
    try:
        if os_path.exists(file_path):
            os_remove(file_path)
    except Exception:
        pass


def _sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkSenseVoiceWeight(root: str) -> bool:
    """モデルファイルが揃い、取得時に大きさとハッシュを確かめてから変わって
    いなければ True。sherpa_onnx が使えるかは見ない
    (isSenseVoiceRuntimeInstalled / loadSenseVoiceRuntime で別に確かめる)。"""
    directory = _weightDir(root)
    for filename, (size, _) in _FILES.items():
        file_path = os_path.join(directory, filename)
        if not os_path.isfile(file_path) or os_path.getsize(file_path) != size:
            return False
    return isWeightVerifiedCache(directory)


def _downloadVerified(directory: str, filename: str, callback: Optional[Callable[[float], None]]) -> bool:
    """一時ファイルに取得し、大きさと SHA-256 が合ったときだけ置き換える。
    途中で終わっても (アプリを閉じた等) 本来のファイル名には何も残らない。"""
    import huggingface_hub
    size, sha256 = _FILES[filename]
    file_path = os_path.join(directory, filename)
    # 前回の取得で照合済みのファイルは取り直さない (tokens.txt だけ失敗した
    # ときに、モデル本体の 239MB を落とし直さないように)。
    if os_path.isfile(file_path) and os_path.getsize(file_path) == size and _sha256(file_path) == sha256:
        return True
    tmp_path = file_path + ".tmp"
    url = huggingface_hub.hf_hub_url(SENSEVOICE_REPO_ID, filename, revision=SENSEVOICE_REVISION)
    try:
        if not downloadFile(url, tmp_path, func=callback):
            return False
        if os_path.getsize(tmp_path) != size or _sha256(tmp_path) != sha256:
            return False
        os_replace(tmp_path, file_path)
        return True
    finally:
        _removeQuietly(tmp_path)


def downloadSenseVoiceWeight(
    root: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """モデルが無ければ Hugging Face から取得する。成功したら True。
    どこで失敗しても end_callback は必ず呼ぶ (画面の「取得中」を解くため)。"""
    try:
        if checkSenseVoiceWeight(root):
            return True
        directory = _weightDir(root)
        os_makedirs(directory, exist_ok=True)
        for filename in _FILES:
            func = callback if filename == _MODEL_FILE else None
            if not _downloadVerified(directory, filename, func):
                return False
        writeWeightVerifiedCache(directory)
        return checkSenseVoiceWeight(root)
    except Exception:
        errorLogging()
        return False
    finally:
        if callable(end_callback):
            end_callback()


class SenseVoiceRecognizer:
    """sherpa-onnx の OfflineRecognizer を包む。マイクとスピーカーで同じ
    インスタンスを共有するため、推論は1つずつ順に行う (SenseVoice は音声長の
    数%で終わるので、順番待ちの遅れは小さい)。"""

    def __init__(self, recognizer) -> None:
        self._recognizer = recognizer
        self._lock = Lock()

    def decode(self, audio: np.ndarray, sample_rate: int) -> Tuple[str, str]:
        """(text, lang) を返す。lang は "ja" などで、"<|ja|>" の囲みは外す。"""
        with self._lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(sample_rate, audio)
            self._recognizer.decode_stream(stream)
            result = stream.result
        return result.text or "", (result.lang or "").strip("<|>")


# 重みのディレクトリ -> [SenseVoiceRecognizer, 使っている AudioTranscriber の数]
_recognizers: Dict[str, list] = {}
_recognizers_lock = Lock()
# 読み込み (数秒) の間も _recognizers_lock を持たないよう、読み込みは別の
# ロックで1つずつ行う。
_load_lock = Lock()


def _loadRecognizer(directory: str) -> SenseVoiceRecognizer:
    module = loadSenseVoiceRuntime()
    if module is None:
        raise SenseVoiceRuntimeError("sherpa_onnx could not be loaded")
    try:
        recognizer = module.OfflineRecognizer.from_sense_voice(
            model=os_path.join(directory, _MODEL_FILE),
            tokens=os_path.join(directory, _TOKENS_FILE),
            # マイクとスピーカーで1つを共有し推論は順番に行うので、同時に動く
            # のはいつも1つ。Whisper の1モデル分と同じ数を使う。
            num_threads=transcriptionCpuThreads(),
            language="auto",
            use_itn=True,
        )
    except Exception:
        errorLogging()
        raise
    return SenseVoiceRecognizer(recognizer)


def acquireSenseVoiceRecognizer(root: str) -> SenseVoiceRecognizer:
    """言語を自動判定する認識器を返し、使っている数を1つ増やす。

    マイクとスピーカーで同じものを共有し、読み込みは最初の1回だけ。使い
    終わったら releaseSenseVoiceRecognizer で返す (0 になったら解放する)。
    言語を固定した認識器は作らない (候補が1言語でも、判定が外れた結果は
    捨てる。SenseVoiceProvider 参照)。1つで約 240MB あるため。
    """
    directory = _weightDir(root)
    with _load_lock:
        with _recognizers_lock:
            entry = _recognizers.get(directory)
            if entry is not None:
                entry[1] += 1
                return entry[0]
        recognizer = _loadRecognizer(directory)
        with _recognizers_lock:
            _recognizers[directory] = [recognizer, 1]
        return recognizer


def releaseSenseVoiceRecognizer(root: str) -> None:
    """acquireSenseVoiceRecognizer で得たものを返す。誰も使わなくなったら
    解放する (ほかのエンジンに切り替えたあとまでメモリに残さない)。"""
    directory = _weightDir(root)
    with _recognizers_lock:
        entry = _recognizers.get(directory)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] <= 0:
            del _recognizers[directory]
