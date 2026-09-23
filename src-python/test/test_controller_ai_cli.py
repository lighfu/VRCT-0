import unittest
from unittest.mock import MagicMock, patch

import controller as controller_module
import mainloop
from config import config
from controller import Controller
from errors import ErrorCode


class _ConfigGuard(unittest.TestCase):
    def setUp(self):
        self._orig = (config.SELECTED_AI_CLI_TOOL, dict(config.SELECTED_AI_CLI_MODELS),
                      list(config.SELECTABLE_AI_CLI_TOOL_LIST), list(config.SELECTABLE_AI_CLI_MODEL_LIST),
                      dict(config._SELECTABLE_TRANSLATION_ENGINE_STATUS))

    def tearDown(self):
        (config.SELECTED_AI_CLI_TOOL, config.SELECTED_AI_CLI_MODELS, config.SELECTABLE_AI_CLI_TOOL_LIST,
         config.SELECTABLE_AI_CLI_MODEL_LIST, config.SELECTABLE_TRANSLATION_ENGINE_STATUS) = self._orig


class AiCliConfigTests(_ConfigGuard):
    def test_selected_model_is_remembered_per_tool(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODEL = "sonnet"
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODEL = "gpt-5.5"
        config.SELECTED_AI_CLI_TOOL = "claude"
        self.assertEqual(config.SELECTED_AI_CLI_MODEL, "sonnet")
        self.assertEqual(config.SELECTED_AI_CLI_MODELS["codex"], "gpt-5.5")

    def test_setting_none_clears_the_tool_model(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODEL = None
        self.assertIsNone(config.SELECTED_AI_CLI_MODEL)

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(Exception):
            config.SELECTED_AI_CLI_TOOL = "notepad"


class AiCliEndpointTests(_ConfigGuard):
    def _controller(self):
        controller = Controller.__new__(Controller)
        controller.run_mapping = mainloop.run_mapping
        controller.run = MagicMock()
        controller.updateTranslationEngineAndEngineList = lambda: None
        return controller

    def test_endpoints_are_routed(self):
        for endpoint in ("/get/data/connected_ai_cli", "/run/ai_cli_connection",
                         "/get/data/selectable_ai_cli_model_list", "/get/data/selected_ai_cli_model",
                         "/set/data/selected_ai_cli_model", "/get/data/selectable_ai_cli_tool_list",
                         "/get/data/selected_ai_cli_tool", "/set/data/selected_ai_cli_tool"):
                with self.subTest(endpoint=endpoint):
                    self.assertIn(endpoint, mainloop.mapping)
        for key in ("selectable_ai_cli_model_list", "selected_ai_cli_model", "ai_cli_connection"):
            self.assertIn(key, mainloop.run_mapping)

    def test_set_tool_switches_and_rechecks_connection(self):
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["codex", "claude"]
        controller = self._controller()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            response = controller.setSelectedAiCliTool("codex")
        self.assertEqual(response, {"status": 200, "result": "codex"})
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "codex")
        mock_model.setTranslatorAiCliTool.assert_called_once_with("codex")
        self.assertEqual(config.SELECTABLE_AI_CLI_MODEL_LIST, ["gpt-5.5"])
        controller.run.assert_any_call(200, "/run/ai_cli_connection", True)

    def test_set_tool_rejects_tool_that_is_not_installed(self):
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["claude"]
        config.SELECTED_AI_CLI_TOOL = "claude"
        controller = self._controller()
        with patch.object(controller_module, "model"):
            response = controller.setSelectedAiCliTool("agy")
        self.assertNotEqual(response["status"], 200)
        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_AI_CLI_FAILED.value)
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "claude")

    def test_shutdown_closes_ai_cli_sessions(self):
        import inspect
        source = inspect.getsource(Controller.shutdown)
        self.assertIn("model.closeTranslatorAiCli", source)


class AiCliInitTests(_ConfigGuard):
    def test_init_has_an_explicit_ai_cli_case(self):
        # case が無いと既定の `case _: status = connected_network is True` に落ち、
        # CLI が無い PC でもネット接続だけで「使える」扱いになる。
        import inspect
        self.assertIn('case "AI_CLI":', inspect.getsource(Controller.init))

    def test_init_marks_ai_cli_unavailable_when_no_cli_installed(self):
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = []
            self.assertEqual(Controller._checkAiCliAtStartup(), (False, None, None))
        mock_model.authenticationTranslatorAiCli.assert_not_called()
        self.assertEqual(config.SELECTABLE_AI_CLI_TOOL_LIST, [])

    def test_startup_switches_to_an_installed_tool_and_keeps_saved_model(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODELS = {"codex": "gpt-6-sol", "claude": "haiku", "agy": ""}
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5", "gpt-6-sol"]
            self.assertEqual(Controller._checkAiCliAtStartup(), (True, ["gpt-5.5", "gpt-6-sol"], "gpt-6-sol"))
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "codex")

    def test_startup_falls_back_to_first_model_when_saved_one_is_gone(self):
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "old-model", "claude": "haiku", "agy": ""}
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            self.assertEqual(Controller._checkAiCliAtStartup(), (True, ["gpt-5.5"], "gpt-5.5"))


if __name__ == "__main__":
    unittest.main()
