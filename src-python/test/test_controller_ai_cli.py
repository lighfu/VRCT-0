import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import config as config_module
import controller as controller_module
import mainloop
from config import config
from controller import Controller
from errors import ErrorCode


class _ConfigGuard(unittest.TestCase):
    def setUp(self):
        self._orig = (config.SELECTED_AI_CLI_TOOL, dict(config.SELECTED_AI_CLI_MODELS),
                      list(config.SELECTABLE_AI_CLI_TOOL_LIST), list(config.SELECTABLE_AI_CLI_MODEL_LIST),
                      dict(config._SELECTABLE_TRANSLATION_ENGINE_STATUS), dict(config.SELECTED_TRANSLATION_ENGINES),
                      config._AI_CLI_TOOL_PREFERENCE)

    def tearDown(self):
        (config.SELECTED_AI_CLI_TOOL, config.SELECTED_AI_CLI_MODELS, config.SELECTABLE_AI_CLI_TOOL_LIST,
         config.SELECTABLE_AI_CLI_MODEL_LIST, config.SELECTABLE_TRANSLATION_ENGINE_STATUS,
         config.SELECTED_TRANSLATION_ENGINES, config._AI_CLI_TOOL_PREFERENCE) = self._orig

    def _controller(self):
        controller = Controller.__new__(Controller)
        controller.run_mapping = mainloop.run_mapping
        controller.run = MagicMock()
        controller.updateTranslationEngineAndEngineList = lambda: None
        return controller

    def _useTabs(self, *engines):
        config.SELECTED_TRANSLATION_ENGINES = {str(i + 1): engine for i, engine in enumerate(engines)}


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

    def test_models_set_to_null_by_hand_does_not_break_the_model(self):
        # config.json を手で書き換えて "SELECTED_AI_CLI_MODELS": null にされた場合。
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODELS = None
        self.assertIsNone(config.SELECTED_AI_CLI_MODEL)
        config.SELECTED_AI_CLI_MODEL = "haiku"
        self.assertEqual(config.SELECTED_AI_CLI_MODEL, "haiku")


class AiCliEndpointTests(_ConfigGuard):
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
        # UI はこの data (今の CLI) で選択を元に戻す (_useBackendErrorHandling.js)。
        self.assertEqual(response["result"]["data"], "claude")
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "claude")

    def test_shutdown_closes_ai_cli_sessions(self):
        import inspect
        source = inspect.getsource(Controller.shutdown)
        self.assertIn("model.closeTranslatorAiCli", source)

    def test_failed_check_keeps_the_remembered_model_but_tells_the_ui_none(self):
        # 一度の接続確認の失敗 (オフライン・一覧の取得失敗) で、CLI ごとに覚えた
        # モデルを消さない。UI にはモデル一覧 [] と選択モデル None を送る。
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "gpt-6-sol", "claude": "haiku", "agy": ""}
        controller = self._controller()
        for label, setup in (("check failed", lambda m: setattr(m.authenticationTranslatorAiCli, "return_value", False)),
                             ("no models", lambda m: (setattr(m.authenticationTranslatorAiCli, "return_value", True),
                                                      setattr(m.getTranslatorAiCliModelList, "return_value", []))),
                             ("exception", lambda m: setattr(m.authenticationTranslatorAiCli, "side_effect", RuntimeError("x")))):
            with self.subTest(label), patch.object(controller_module, "model") as mock_model, \
                 patch.object(controller_module, "errorLogging"):
                controller.run.reset_mock()
                setup(mock_model)
                response = controller.checkTranslatorAiCliConnection()
                self.assertEqual(response["status"], 400)
                self.assertEqual(config.SELECTED_AI_CLI_MODEL, "gpt-6-sol")
                self.assertEqual(config.SELECTED_AI_CLI_MODELS["codex"], "gpt-6-sol")
                controller.run.assert_any_call(200, "/run/selectable_ai_cli_model_list", [])
                controller.run.assert_any_call(200, "/run/selected_ai_cli_model", None)
                self.assertFalse(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"])

    def test_status_change_updates_the_connection_indicator(self):
        controller = self._controller()
        controller._onAiCliStatusChange(False)
        status, endpoint, result = controller.run.call_args.args
        self.assertEqual((status, endpoint), (400, "/run/ai_cli_connection"))
        self.assertEqual(result["error_code"], ErrorCode.CONNECTION_AI_CLI_FAILED.value)
        self.assertIs(result["data"], False)
        controller._onAiCliStatusChange(True)
        controller.run.assert_called_with(200, "/run/ai_cli_connection", True)

    def test_bootstrap_registers_the_status_callback(self):
        controller = Controller.__new__(Controller)
        controller._model = MagicMock()
        controller._bootstrapModel()
        controller._model.setTranslatorAiCliStatusCallback.assert_called_once_with(controller._onAiCliStatusChange)

    def test_ai_cli_endpoints_run_one_at_a_time(self):
        # CLI の切り替えとモデルの選択が並行すると、あるCLIのモデルを別のCLIの欄に保存しうる。
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["codex", "claude"]
        config.SELECTED_AI_CLI_TOOL = "claude"
        controller = self._controller()
        entered_switch = threading.Event()
        release_switch = threading.Event()
        order = []
        with patch.object(controller_module, "model") as mock_model:
            def slowSwitch(tool):
                order.append("switch-start")
                entered_switch.set()
                release_switch.wait(5)
                order.append("switch-end")
            mock_model.setTranslatorAiCliTool.side_effect = slowSwitch
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            mock_model.setTranslatorAiCliModel.side_effect = lambda model: order.append("set-model") or True
            switch = threading.Thread(target=controller.setSelectedAiCliTool, args=("codex",))
            switch.start()
            self.assertTrue(entered_switch.wait(5))
            select = threading.Thread(target=controller.setTranslatorAiCliModel, args=("gpt-5.5",))
            select.start()
            time.sleep(0.2)
            self.assertNotIn("set-model", order)
            release_switch.set()
            switch.join(5)
            select.join(5)
        self.assertEqual(order[:2], ["switch-start", "switch-end"])
        self.assertIn("set-model", order[2:])


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

    def test_startup_fallback_tool_is_not_saved(self):
        # 保存していた claude が一時的に見つからなかっただけなら、次の起動で claude に戻る。
        config.SELECTED_AI_CLI_TOOL = "claude"
        serialize = config_module.json_serializable_vars["SELECTED_AI_CLI_TOOL"]
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            Controller._checkAiCliAtStartup()
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "codex")
        self.assertEqual(serialize(config), "claude")
        # ユーザーが CLI を選び直したら、それを保存する。
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["codex"]
        controller = self._controller()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            controller.setSelectedAiCliTool("codex")
        self.assertEqual(serialize(config), "codex")

    def test_startup_falls_back_to_first_model_when_saved_one_is_gone(self):
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "old-model", "claude": "haiku", "agy": ""}
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            self.assertEqual(Controller._checkAiCliAtStartup(), (True, ["gpt-5.5"], "gpt-5.5"))

    def test_startup_without_ai_cli_tabs_does_not_list_models(self):
        # どのタブも AI CLI を使っていなければ、起動中に `codex debug models` などを走らせない。
        config.SELECTED_AI_CLI_TOOL = "codex"
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            self.assertEqual(Controller._checkAiCliAtStartup(list_models=False), (True, None, None))
        mock_model.getTranslatorAiCliModelList.assert_not_called()

    def test_init_lists_models_only_when_a_tab_uses_ai_cli(self):
        import inspect
        source = inspect.getsource(Controller.init)
        self.assertIn('list_models="AI_CLI" in config.SELECTED_TRANSLATION_ENGINES.values()', source)
        self.assertIn("self._listAiCliModelsInBackground()", source)

    def test_apply_warms_up_only_when_a_tab_uses_ai_cli(self):
        for tabs, expected in ((("CTranslate2", "CTranslate2", "CTranslate2"), False),
                               (("CTranslate2", "AI_CLI", "CTranslate2"), True)):
            with self.subTest(tabs=tabs), patch.object(controller_module, "model") as mock_model:
                self._useTabs(*tabs)
                config.SELECTED_AI_CLI_TOOL = "claude"
                config.SELECTABLE_AI_CLI_MODEL_LIST = ["haiku", "sonnet"]
                Controller._applyAiCliStartupResult(["haiku", "sonnet"], "sonnet")
                mock_model.setTranslatorAiCliModel.assert_called_once_with("sonnet")
                mock_model.getTranslatorAiCliModelList.assert_not_called()
                self.assertEqual(mock_model.updateTranslatorAiCliClient.called, expected)
                self.assertEqual(config.SELECTED_AI_CLI_MODEL, "sonnet")

    def test_background_listing_selects_the_remembered_model(self):
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"] = True
        config.SELECTABLE_AI_CLI_MODEL_LIST = []
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "gpt-6-sol", "claude": "haiku", "agy": ""}
        controller = self._controller()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5", "gpt-6-sol"]
            thread = controller._listAiCliModelsInBackground()
            thread.join(5)
        self.assertTrue(thread.daemon)
        self.assertEqual(config.SELECTABLE_AI_CLI_MODEL_LIST, ["gpt-5.5", "gpt-6-sol"])
        mock_model.setTranslatorAiCliModel.assert_called_once_with("gpt-6-sol")
        controller.run.assert_any_call(200, "/run/selectable_ai_cli_model_list", ["gpt-5.5", "gpt-6-sol"])
        controller.run.assert_any_call(200, "/run/selected_ai_cli_model", "gpt-6-sol")

    def test_background_listing_failure_keeps_the_remembered_model(self):
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"] = True
        config.SELECTABLE_AI_CLI_MODEL_LIST = []
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "gpt-6-sol", "claude": "haiku", "agy": ""}
        controller = self._controller()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliModelList.return_value = []
            controller._listAiCliModelsInBackground().join(5)
        self.assertFalse(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"])
        self.assertEqual(config.SELECTED_AI_CLI_MODELS["codex"], "gpt-6-sol")
        controller.run.assert_any_call(200, "/run/ai_cli_connection", False)

    def test_background_listing_is_skipped_when_not_needed(self):
        controller = self._controller()
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"] = False
        self.assertIsNone(controller._listAiCliModelsInBackground())
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS["AI_CLI"] = True
        config.SELECTABLE_AI_CLI_MODEL_LIST = ["haiku"]
        self.assertIsNone(controller._listAiCliModelsInBackground())


if __name__ == "__main__":
    unittest.main()
