"""Tests for models.ocr.ocr_engine_rapidocr: モデルの置き場所。

rapidocr は同梱していないモデル (ハングル・タイ文字などの PP-OCRv5) を初めて使うときに取りに行く。
何も渡さないとパッケージの中 (入れた版では current\\_internal\\rapidocr\\models) に置き、
更新のたびに消える。取ってくるものは PATH_DATA\\weights\\rapidocr に置き、同梱のモデルは
パッケージの中のものをそのまま使うことを確かめる。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.ocr import ocr_engine_rapidocr as engine
from models.ocr.ocr_languages import OcrModelSpec

# rapidocr 3.9.2 の wheel に入っているモデル (default_models.yaml の onnxruntime の表での名前)。
BUNDLED = (
    "PP-OCRv6_det_small.onnx",
    "PP-OCRv6_rec_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)


def _params(spec: OcrModelSpec) -> dict:
    from rapidocr import EngineType, LangRec, ModelType, OCRVersion  # type: ignore

    params = {
        "Global.log_level": "error",
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Cls.engine_type": EngineType.ONNXRUNTIME,
        "Det.ocr_version": OCRVersion(spec.ocr_version),
        "Rec.ocr_version": OCRVersion(spec.ocr_version),
        "Det.model_type": ModelType(spec.model_type),
        "Rec.model_type": ModelType(spec.model_type),
    }
    if spec.lang_rec:
        params["Rec.lang_type"] = LangRec(spec.lang_rec)
    return params


@unittest.skipUnless(engine.isAvailable(), "rapidocr is not installed")
class TestModelLocationParams(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bundled = Path(self._tmp.name) / "bundled"
        self.bundled.mkdir()
        for name in BUNDLED:
            (self.bundled / name).write_bytes(b"onnx")
        self.download = os.path.join(self._tmp.name, "data", "weights", "rapidocr")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_without_a_download_dir_nothing_is_added(self) -> None:
        params = _params(OcrModelSpec("PP-OCRv6", "small"))
        self.assertEqual(engine.modelLocationParams(params, self.bundled, None), params)

    def test_bundled_models_are_used_in_place(self) -> None:
        out = engine.modelLocationParams(_params(OcrModelSpec("PP-OCRv6", "small")), self.bundled, self.download)
        self.assertEqual(out["Global.model_root_dir"], self.download)
        self.assertEqual(out["Det.model_path"], str(self.bundled / "PP-OCRv6_det_small.onnx"))
        self.assertEqual(out["Rec.model_path"], str(self.bundled / "PP-OCRv6_rec_small.onnx"))
        self.assertEqual(out["Cls.model_path"], str(self.bundled / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"))

    def test_models_that_are_not_bundled_go_to_the_download_dir(self) -> None:
        out = engine.modelLocationParams(
            _params(OcrModelSpec("PP-OCRv5", "mobile", "korean")), self.bundled, self.download)
        # 韓国語の rec と PP-OCRv5 の det は同梱していないので、model_path を付けず、
        # rapidocr に Global.model_root_dir (データの置き場所) へ取りに行かせる。
        self.assertEqual(out["Global.model_root_dir"], self.download)
        self.assertNotIn("Det.model_path", out)
        self.assertNotIn("Rec.model_path", out)
        self.assertEqual(out["Cls.model_path"], str(self.bundled / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"))

    def test_the_input_params_are_not_changed(self) -> None:
        params = _params(OcrModelSpec("PP-OCRv6", "small"))
        before = dict(params)
        engine.modelLocationParams(params, self.bundled, self.download)
        self.assertEqual(params, before)

    def test_reader_gets_the_download_dir(self) -> None:
        seen = {}

        def fakeRapidOCR(params):
            seen.update(params)
            return object()

        spec = OcrModelSpec("PP-OCRv5", "mobile", "th")
        with mock.patch.object(engine, "RapidOCR", side_effect=fakeRapidOCR), \
                mock.patch.object(engine, "_bundledModelDirectory", return_value=self.bundled), \
                mock.patch.dict(engine._engine_cache, clear=True), \
                mock.patch.object(engine, "_download_dir", None):
            engine.setModelDirectory(self.download)
            self.assertIsNotNone(engine.getReader(spec))
        self.assertEqual(seen["Global.model_root_dir"], self.download)
        self.assertNotIn("Rec.model_path", seen)


class TestModelPassesTheDataDir(unittest.TestCase):
    def test_start_ocr_capture_sets_the_download_dir_under_path_data(self) -> None:
        import model as model_module
        from model import model
        from models.ocr import OcrPipeline

        class StopHere(OcrPipeline):
            def __init__(self, *args, **kwargs):
                raise RuntimeError("stop here")

        with mock.patch.object(model, "ocr_pipeline", None, create=True), \
                mock.patch.object(model, "ensure_initialized"), \
                mock.patch.object(model_module, "OcrPipeline", StopHere), \
                mock.patch.object(model_module, "isSupportedOcrLanguage", return_value=True), \
                mock.patch.object(model_module, "errorLogging"), \
                mock.patch.object(model_module.ocr_engine_rapidocr, "setModelDirectory") as set_dir:
            self.assertFalse(model.startOCRCapture(lambda _: None))
        set_dir.assert_called_once_with(
            os.path.join(model_module.config.PATH_DATA, "weights", "rapidocr"))


if __name__ == "__main__":
    unittest.main()
