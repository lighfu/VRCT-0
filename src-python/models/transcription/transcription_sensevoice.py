"""ローカル SenseVoice (sherpa-onnx) による文字起こし。

SenseVoice-Small は非自己回帰型のため、Whisper のように1トークンずつ
生成せず1回の推論で全文を出す。CPU でも音声の長さの数%の時間で終わり、
日本語の精度は Whisper large-v3-turbo と同程度 (Common Voice 日本語 60文、
4スレッド CPU で CER 16.1% / 処理時間は音声長の 0.04 倍。large-v3-turbo-int8
は CER 15.6% / 1.0 倍、base は CER 32.5% / 0.15 倍)。
対応言語は日本語・英語・中国語・広東語・韓国語。

モデルは Hugging Face から weights/sensevoice/ に取得する (int8 量子化版、
約 230MB)。2025-09-09 版は同じ評価で CER 79% と大きく劣化したため、
2024-07-17 版を使う。
"""

from os import path as os_path, makedirs as os_makedirs
from typing import Callable, Optional

from models.transcription.transcription_whisper import downloadFile
from utils import errorLogging

# UI のダウンロード欄 (weight_download_status) で使う重みの ID。重みは1種類だけ。
SENSEVOICE_WEIGHT_TYPE = "sensevoice-small"
SENSEVOICE_REPO_ID = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
_MODEL_FILE = "model.int8.onnx"
_TOKENS_FILE = "tokens.txt"
_FILENAMES = (_MODEL_FILE, _TOKENS_FILE)

sherpa_onnx = None  # 使うときに _sherpaOnnx() が読み込む (起動時間の短縮)


def _sherpaOnnx():
    global sherpa_onnx
    if sherpa_onnx is None:
        try:
            import sherpa_onnx as _module
        except Exception:
            return None
        sherpa_onnx = _module
    return sherpa_onnx


def _weightDir(root: str) -> str:
    return os_path.join(root, "weights", "sensevoice")


def checkSenseVoiceWeight(root: str) -> bool:
    """モデルファイルが揃っていて、sherpa-onnx が読み込める状態なら True。"""
    directory = _weightDir(root)
    for filename in _FILENAMES:
        file_path = os_path.join(directory, filename)
        if not os_path.isfile(file_path) or os_path.getsize(file_path) == 0:
            return False
    return _sherpaOnnx() is not None


def downloadSenseVoiceWeight(
    root: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """モデルが無ければ Hugging Face から取得する。成功したら True。"""
    directory = _weightDir(root)
    os_makedirs(directory, exist_ok=True)
    ok = True
    if not checkSenseVoiceWeight(root):
        import huggingface_hub
        for filename in _FILENAMES:
            url = huggingface_hub.hf_hub_url(SENSEVOICE_REPO_ID, filename)
            func = callback if filename == _MODEL_FILE else None
            ok = downloadFile(url, os_path.join(directory, filename), func=func) and ok
    if callable(end_callback):
        end_callback()
    return ok and checkSenseVoiceWeight(root)


def getSenseVoiceRecognizer(root: str, num_threads: int = 4):
    """sherpa-onnx の OfflineRecognizer を返す。

    言語は "auto" (自動判定) で作る。sherpa-onnx は言語をモデル読み込み時に
    固定するため、言語ごとに読み込むとメモリを言語数ぶん使う。自動判定の
    結果 (`stream.result.lang`) は呼び出し側 (SenseVoiceProvider) が候補言語と
    突き合わせる。
    """
    module = _sherpaOnnx()
    if module is None:
        raise RuntimeError("sherpa_onnx is not installed")
    directory = _weightDir(root)
    try:
        return module.OfflineRecognizer.from_sense_voice(
            model=os_path.join(directory, _MODEL_FILE),
            tokens=os_path.join(directory, _TOKENS_FILE),
            num_threads=num_threads,
            language="auto",
            use_itn=True,
        )
    except Exception:
        errorLogging()
        raise
