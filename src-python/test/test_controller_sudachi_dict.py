import unittest
from unittest.mock import MagicMock, patch

import controller as controller_module
import mainloop
from config import config
from controller import Controller
from errors import ErrorCode


class SudachiDictEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_type = config.SUDACHI_DICT_TYPE
        self._original_dict = dict(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        config.SUDACHI_DICT_TYPE = self._original_type
        config.SELECTABLE_SUDACHI_DICT_TYPE_DICT.update(self._original_dict)

    def test_endpoints_are_routed(self) -> None:
        for endpoint in (
            "/get/data/selectable_sudachi_dict_type_dict",
            "/get/data/selected_sudachi_dict_type",
            "/set/data/selected_sudachi_dict_type",
            "/run/download_sudachi_dict",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, mainloop.mapping)
        for key in ("download_progress_sudachi_dict", "downloaded_sudachi_dict", "error_sudachi_dict"):
            with self.subTest(run_mapping=key):
                self.assertIn(key, mainloop.run_mapping)

    def test_default_type_is_core(self) -> None:
        self.assertIn(config.SUDACHI_DICT_TYPE, ["core", "full"])
        self.assertEqual(config.SELECTABLE_SUDACHI_DICT_TYPE_LIST, ["core", "full"])
        self.assertTrue(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["core"])

    def test_set_type_restarts_transliteration(self) -> None:
        with patch.object(controller_module, "model") as mock_model:
            response = Controller.setSudachiDictType("full")
        self.assertEqual(response, {"status": 200, "result": "full"})
        mock_model.restartTransliteration.assert_called_once()

    def test_set_type_rejects_unknown_value(self) -> None:
        with patch.object(controller_module, "model"):
            response = Controller.setSudachiDictType("huge")
        self.assertNotEqual(response["status"], 200)

    def test_downloaded_marks_full_available(self) -> None:
        run = MagicMock()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = True
            handler = Controller.DownloadSudachiDict(mainloop.run_mapping, run)
            handler.downloaded()
        self.assertTrue(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"])
        run.assert_called_once_with(200, "/run/downloaded_sudachi_dict", "full")

    def test_failed_download_reports_error_code(self) -> None:
        run = MagicMock()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = False
            handler = Controller.DownloadSudachiDict(mainloop.run_mapping, run)
            handler.downloaded()
        status, endpoint, result = run.call_args.args
        self.assertEqual(endpoint, "/run/error_sudachi_dict")
        self.assertEqual(result["error_code"], ErrorCode.WEIGHT_SUDACHI_DICT_DOWNLOAD.value)

    def test_selectable_dict_reports_full_missing_after_deletion(self) -> None:
        config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"] = True
        with patch.object(controller_module, "model") as mock_model:
            mock_model.checkSudachiFullDict.return_value = False
            Controller.updateDownloadedSudachiDict()
        self.assertFalse(config.SELECTABLE_SUDACHI_DICT_TYPE_DICT["full"])
