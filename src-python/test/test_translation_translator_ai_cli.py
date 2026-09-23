import unittest
from unittest.mock import MagicMock, patch

from errors import ErrorCode
from models.translation import translation_translator as tt_module
from models.translation.translation_providers import CONNECTION_PROVIDER_REGISTRY
from models.translation.translation_translator import Translator


class TranslatorAiCliTests(unittest.TestCase):
    def test_check_client_connects_when_tool_is_installed(self):
        translator = Translator()
        fake_client = MagicMock()
        fake_client.setTool.return_value = True
        fake_client.authenticationCheck.return_value = True
        with patch.object(tt_module, "AICliClient", return_value=fake_client):
            self.assertTrue(translator.checkAiCliClient("claude", root_path="."))
        self.assertTrue(translator.getAiCliConnected())
        fake_client.setTool.assert_called_once_with("claude")

    def test_check_client_passes_version_resets_breaker_and_registers_status(self):
        translator = Translator()
        fake_client = MagicMock()
        fake_client.setTool.return_value = True
        fake_client.authenticationCheck.return_value = True
        with patch.object(tt_module, "AICliClient", return_value=fake_client) as client_class:
            translator.checkAiCliClient("claude", root_path=".", client_version="3.0.0")
        self.assertEqual(client_class.call_args.kwargs["client_version"], "3.0.0")
        fake_client.setStatusCallback.assert_called_once_with(translator._onAiCliStatus)
        fake_client.resetBreaker.assert_called_once()

    def test_status_change_updates_connected_and_forwards(self):
        translator = Translator()
        seen = []
        translator.setAiCliStatusCallback(seen.append)
        translator.ai_cli_connected = True
        translator._onAiCliStatus(False)
        self.assertFalse(translator.getAiCliConnected())
        translator._onAiCliStatus(True)
        self.assertTrue(translator.getAiCliConnected())
        self.assertEqual(seen, [False, True])

    def test_check_client_fails_and_closes_when_tool_missing(self):
        translator = Translator()
        fake_client = MagicMock()
        fake_client.setTool.return_value = False
        with patch.object(tt_module, "AICliClient", return_value=fake_client):
            self.assertFalse(translator.checkAiCliClient("agy", root_path="."))
        self.assertFalse(translator.getAiCliConnected())
        fake_client.close.assert_called_once()

    def test_translate_dispatches_to_ai_cli_client(self):
        translator = Translator()
        translator.ai_cli_client = MagicMock()
        translator.ai_cli_client.translate.return_value = "Hello"
        history = [{"source": "chat", "text": "x"}]
        result = translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは", context_history=history)
        self.assertEqual(result, "Hello")
        translator.ai_cli_client.setContextHistory.assert_called_once_with(history)

    def test_translate_without_client_returns_false(self):
        translator = Translator()
        self.assertFalse(translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは"))

    def test_translate_error_returns_false(self):
        translator = Translator()
        translator.ai_cli_client = MagicMock()
        translator.ai_cli_client.translate.side_effect = tt_module.AiCliError("timeout")
        self.assertFalse(translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは"))

    def test_registry_entry(self):
        spec = CONNECTION_PROVIDER_REGISTRY["AI_CLI"]
        self.assertEqual(spec.error_connection_failed, ErrorCode.CONNECTION_AI_CLI_FAILED)
        self.assertEqual(spec.error_model_invalid, ErrorCode.MODEL_AI_CLI_INVALID)
        self.assertEqual(spec.selected_model_attr, "SELECTED_AI_CLI_MODEL")

    def test_model_close_closes_the_ai_cli_client(self):
        # アプリ終了時 (Controller.shutdown -> model.closeTranslatorAiCli) に
        # CLI の常駐プロセスを残さない。
        from model import Model
        model = object.__new__(Model)
        model._inited = True
        model._init_failed = False
        model.translator = Translator()
        model.translator.ai_cli_client = MagicMock()
        model.closeTranslatorAiCli()
        model.translator.ai_cli_client.shutdown.assert_called_once()

    def test_model_close_before_init_does_nothing(self):
        from model import Model
        model = object.__new__(Model)
        model._inited = False
        model._init_failed = True
        model.closeTranslatorAiCli()


if __name__ == "__main__":
    unittest.main()
