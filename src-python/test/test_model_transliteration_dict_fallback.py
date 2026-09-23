"""_transliterationDictPath() の Important 1 修正に関するテスト。

SUDACHI_DICT_TYPE が "full" でもフル辞書が未ダウンロードのときは、
黙って None を返すのではなく utils.errorLog() でフォールバックをログに
残したうえで None (= 標準辞書) にフォールバックすることを確認する。
"""

import unittest
from unittest.mock import MagicMock, patch

import model as model_module
from config import config
from model import Model


class TransliterationDictPathFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = Model.__new__(Model)
        self._original_type = config.SUDACHI_DICT_TYPE
        self.addCleanup(setattr, config, "SUDACHI_DICT_TYPE", self._original_type)

    def test_full_missing_returns_none_and_logs(self) -> None:
        config.SUDACHI_DICT_TYPE = "full"
        with patch.object(model_module, "checkSudachiFullDict", return_value=False), \
             patch.object(model_module, "errorLog") as mock_error_log:
            result = self.model._transliterationDictPath()
        self.assertIsNone(result)
        mock_error_log.assert_called_once()

    def test_full_available_returns_path_without_logging(self) -> None:
        config.SUDACHI_DICT_TYPE = "full"
        with patch.object(model_module, "checkSudachiFullDict", return_value=True), \
             patch.object(model_module, "sudachiFullDictPath", return_value="/fake/path") as mock_path, \
             patch.object(model_module, "errorLog") as mock_error_log:
            result = self.model._transliterationDictPath()
        self.assertEqual(result, "/fake/path")
        mock_error_log.assert_not_called()
        mock_path.assert_called_once()

    def test_core_returns_none_without_logging(self) -> None:
        config.SUDACHI_DICT_TYPE = "core"
        with patch.object(model_module, "errorLog") as mock_error_log:
            result = self.model._transliterationDictPath()
        self.assertIsNone(result)
        mock_error_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
