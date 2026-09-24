"""RapidOCR (ONNX) ラッパー。モデル指定ごとに遅延生成した推論器をキャッシュする。

readtext_bgr(crop) -> [{"text": str, "confidence": float}, ...]。
EasyOCRラッパーと同じ形を返すので、パイプライン側の扱いは変わらない。
例外は投げず、失敗時は空リストを返してそのtickを飛ばす。

EasyOCRから乗り換えた理由と実測は ocr_languages.py と
docs/details/ocr.md を参照。onnxruntime は faster-whisper が
Silero VAD 用に既に依存しているので、追加される実行時依存はない。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

import numpy as np

from .ocr_languages import OcrModelSpec

try:
    from utils import errorLogging, printLog
except Exception:  # pragma: no cover
    def errorLogging():
        import traceback
        print(traceback.format_exc())

    def printLog(*args, **kwargs):
        print(*args, **kwargs)

# rapidocr は import に時間がかかる (onnxruntime 等を引き込む) ので、
# 使うときに読み込む (起動時間の短縮, A-1, 2026-09-23)。
RapidOCR = None  # type: ignore
EngineType = LangRec = ModelType = OCRVersion = None  # type: ignore


def _rapidocr():
    global RapidOCR, EngineType, LangRec, ModelType, OCRVersion
    if RapidOCR is None:
        try:
            from rapidocr import EngineType as _EngineType, LangRec as _LangRec, \
                ModelType as _ModelType, OCRVersion as _OCRVersion, RapidOCR as _RapidOCR  # type: ignore
        except Exception:
            errorLogging()
            return None
        RapidOCR, EngineType, LangRec, ModelType, OCRVersion = (
            _RapidOCR, _EngineType, _LangRec, _ModelType, _OCRVersion,
        )
    return RapidOCR

# 吹き出しの切り出しは小さい(短辺100px前後のこともある)。RapidOCRの既定は
# 短辺を736pxまで拡大する設定 (limit_type="min") なので、そのままだと7倍に
# 引き伸ばして1枚2秒近くかかる。320に下げると実測で2倍以上速くなり、
# 精度は落ちなかった (95枚で中央値 1778ms -> 790ms)。
DET_LIMIT_SIDE_LEN = 320

_engine_cache: Dict[OcrModelSpec, object] = {}
_engine_lock = Lock()

# rapidocr は、同梱していないモデル (ハングル・タイ文字などの PP-OCRv5 のモデル) を初めて使うときに
# 取りに行く。何も渡さないと置き場所は rapidocr のパッケージの中 (入れた版では
# current\_internal\rapidocr\models) で、更新のたびに消える。取ってくるものは
# setModelDirectory で渡された場所 (PATH_DATA\weights\rapidocr) に置き、同梱のモデル
# (PP-OCRv6 small の det/rec と cls) はパッケージの中のものをそのまま使う。
_download_dir: Optional[str] = None


def setModelDirectory(path: Optional[str]) -> None:
    """取ってくるモデルの置き場所。作り直すまでは今の推論器を使い続ける。"""
    global _download_dir
    _download_dir = path


def _bundledModelDirectory() -> Optional[Path]:
    try:
        import rapidocr  # type: ignore
    except Exception:
        return None
    return Path(rapidocr.__file__).resolve().parent / "models"


def modelLocationParams(params: dict, bundled_dir: Optional[Path], download_dir: Optional[str]) -> dict:
    """params に、モデルの置き場所の指定を足した新しい dict を返す。

    download_dir が無ければ何も足さない (rapidocr の既定のまま)。あれば、取ってくるモデルは
    そこへ置かせ、同梱のフォルダ bundled_dir にあるモデルは model_path でそれを指す。
    どのファイルを使うかは rapidocr 自身の表 (default_models.yaml) で調べる。
    """
    out = dict(params)
    if not download_dir:
        return out
    out["Global.model_root_dir"] = str(download_dir)
    if bundled_dir is None:
        return out
    try:
        from rapidocr.inference_engine.base import FileInfo, InferSession  # type: ignore
        from rapidocr.main import DEFAULT_CFG_PATH  # type: ignore
        from rapidocr.utils.parse_parameters import ParseParams  # type: ignore

        cfg = ParseParams.update_batch(ParseParams.load(DEFAULT_CFG_PATH), params)
        for section in ("Det", "Cls", "Rec"):
            task = cfg[section]
            info = InferSession.get_model_url(FileInfo(
                engine_type=task.engine_type,
                ocr_version=task.ocr_version,
                task_type=task.task_type,
                lang_type=task.lang_type,
                model_type=task.model_type,
            ))
            bundled = Path(bundled_dir) / Path(str(info["model_dir"])).name
            if bundled.is_file():
                out[f"{section}.model_path"] = str(bundled)
    except Exception:
        # 調べられなくても、足りないモデルを download_dir へ取りに行くだけで動きは変わらない。
        errorLogging()
    return out


def isAvailable() -> bool:
    return _rapidocr() is not None


def getReader(spec: OcrModelSpec) -> Optional[object]:
    """モデル指定に対応する推論器を返す。生成に失敗したら None。"""
    if _rapidocr() is None or spec is None:
        return None
    with _engine_lock:
        cached = _engine_cache.get(spec)
        if cached is not None:
            return cached
        try:
            params = {
                # RapidOCRは既定でモデルの読み込み経過をINFOでstderrへ出す。
                # VRCTのstderrはUIのコンソールへ流れて他のログを埋めるので黙らせる。
                "Global.log_level": "error",
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Cls.engine_type": EngineType.ONNXRUNTIME,
                "Det.ocr_version": OCRVersion(spec.ocr_version),
                "Rec.ocr_version": OCRVersion(spec.ocr_version),
                "Det.model_type": ModelType(spec.model_type),
                "Rec.model_type": ModelType(spec.model_type),
                "Det.limit_side_len": DET_LIMIT_SIDE_LEN,
            }
            if spec.lang_rec:
                params["Rec.lang_type"] = LangRec(spec.lang_rec)
            params = modelLocationParams(params, _bundledModelDirectory(), _download_dir)
            engine = RapidOCR(params=params)
        except Exception:
            errorLogging()
            printLog(f"OCR engine: could not load {spec.label}")
            return None
        _engine_cache[spec] = engine
        return engine


def readtext_bgr(reader: object, crop_bgr: np.ndarray, min_confidence: float = 0.5) -> List[dict]:
    if reader is None or crop_bgr is None or crop_bgr.size == 0:
        return []
    try:
        result = reader(crop_bgr)  # type: ignore[operator]
    except Exception:
        errorLogging()
        return []

    # boxes は ndarray なので or でのフォールバックは使えない (真偽判定が曖昧になる)。
    texts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    raw_boxes = getattr(result, "boxes", None)
    boxes = [] if raw_boxes is None else list(raw_boxes)
    out: List[dict] = []
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue
        try:
            confidence = float(scores[index]) if index < len(scores) else 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            continue
        # 行の位置は、行同士をどう繋ぐか (改行か、スペースか、詰めるか) の判断に使う。
        item = {"text": text, "confidence": confidence}
        if index < len(boxes):
            try:
                points = [(float(x), float(y)) for x, y in boxes[index]]
                item["left"] = min(p[0] for p in points)
                item["right"] = max(p[0] for p in points)
                item["top"] = min(p[1] for p in points)
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out
