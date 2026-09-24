"""models/transcription/transcription_sensevoice.py と、SenseVoice の
言語対応表・エンジン一覧への組み込みのテスト。"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from models.transcription import transcription_sensevoice
from models.transcription.transcription_languages import transcription_lang


class TestSenseVoiceLanguages(unittest.TestCase):
    def test_only_supported_languages_have_a_sensevoice_code(self) -> None:
        self.assertEqual(transcription_lang["Japanese"]["Japan"]["SenseVoice"], "ja")
        self.assertEqual(transcription_lang["English"]["United States"]["SenseVoice"], "en")
        self.assertEqual(transcription_lang["Korean"]["South Korea"]["SenseVoice"], "ko")
        self.assertEqual(transcription_lang["Chinese Simplified"]["China"]["SenseVoice"], "zh")
        self.assertEqual(transcription_lang["Chinese Traditional"]["Hong Kong"]["SenseVoice"], "yue")
        self.assertNotIn("SenseVoice", transcription_lang["French"]["France"])

    def test_engine_list_includes_engines_missing_from_the_first_language(self) -> None:
        from config import config
        self.assertIn("SenseVoice", list(config.SELECTABLE_TRANSCRIPTION_ENGINE_LIST))


class TestSenseVoiceWeight(unittest.TestCase):
    def test_check_requires_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as root, \
                patch.object(transcription_sensevoice, "_sherpaOnnx", return_value=MagicMock()):
            directory = os.path.join(root, "weights", "sensevoice")
            os.makedirs(directory)
            self.assertFalse(transcription_sensevoice.checkSenseVoiceWeight(root))
            for name in ("model.int8.onnx", "tokens.txt"):
                with open(os.path.join(directory, name), "wb") as f:
                    f.write(b"x")
            self.assertTrue(transcription_sensevoice.checkSenseVoiceWeight(root))

    def test_check_fails_without_sherpa_onnx(self) -> None:
        with patch.object(transcription_sensevoice, "_sherpaOnnx", return_value=None):
            self.assertFalse(transcription_sensevoice.checkSenseVoiceWeight("/nonexistent"))

    def test_download_fetches_both_files_and_reports_progress_for_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as root, \
                patch.object(transcription_sensevoice, "_sherpaOnnx", return_value=MagicMock()), \
                patch.object(transcription_sensevoice, "downloadFile") as download:
            def _fake_download(url, path, func=None):
                with open(path, "wb") as f:
                    f.write(b"x")
                return True
            download.side_effect = _fake_download
            progress = MagicMock()
            ended = MagicMock()

            ok = transcription_sensevoice.downloadSenseVoiceWeight(root, progress, ended)

        self.assertTrue(ok)
        self.assertEqual(download.call_count, 2)
        funcs = {os.path.basename(c.args[1]): c.kwargs.get("func") for c in download.call_args_list}
        self.assertIs(funcs["model.int8.onnx"], progress)
        self.assertIsNone(funcs["tokens.txt"])
        ended.assert_called_once()


class TestControllerSenseVoiceDownload(unittest.TestCase):
    """ダウンロード完了でエンジンが使える状態になり、UI の重み一覧にも反映される。"""

    def setUp(self) -> None:
        from config import config
        self.config = config
        self._original_status = dict(config._SELECTABLE_TRANSCRIPTION_ENGINE_STATUS)

    def tearDown(self) -> None:
        self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS = self._original_status

    def _download(self, weight_ok: bool):
        from controller import Controller
        calls = []
        run_mapping = {
            "download_progress_sensevoice_weight": "/run/download_progress_sensevoice_weight",
            "downloaded_sensevoice_weight": "/run/downloaded_sensevoice_weight",
            "error_sensevoice_weight": "/run/error_sensevoice_weight",
        }
        download = Controller.DownloadSenseVoice(run_mapping, "sensevoice-small", lambda *a: calls.append(a))
        with patch("controller.model") as mock_model:
            mock_model.checkSenseVoiceModelWeight.return_value = weight_ok
            download.downloaded()
        return calls

    def test_success_enables_the_engine(self) -> None:
        from controller import Controller
        self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"] = False

        calls = self._download(True)

        self.assertEqual(calls, [(200, "/run/downloaded_sensevoice_weight", "sensevoice-small")])
        self.assertTrue(self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"])
        self.assertEqual(
            Controller.getSelectableSenseVoiceWeightTypeDict()["result"], {"sensevoice-small": True}
        )

    def test_failure_reports_an_error(self) -> None:
        self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"] = False

        calls = self._download(False)

        self.assertEqual(calls[0][1], "/run/error_sensevoice_weight")
        self.assertFalse(self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"])


if __name__ == "__main__":
    unittest.main()
