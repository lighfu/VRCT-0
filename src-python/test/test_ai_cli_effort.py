"""AI CLI のエフォート (考える量) を選べるようにした部分のテスト。

選べる値は CLI とモデルで違う: codex は `codex debug models` がモデルごとに返す値、
claude は `--effort` が受け付ける値、agy はモデル名に入っているので選ばせない。
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from models.translation.ai_cli import catalog
from models.translation.ai_cli.claude_session import ClaudeSession
from models.translation.ai_cli.codex_session import CodexSession
from models.translation.translation_ai_cli import AICliClient
from models.translation.translation_languages import loadTranslationLanguages


def _session(cls, effort):
    return cls(command_prefix=["cli"], model="m", workspace=".", base_instructions="b", effort=effort)


class CatalogEffortTests(unittest.TestCase):
    def tearDown(self) -> None:
        catalog._codex_efforts.clear()
        catalog._codex_fast_tiers.clear()

    def test_codex_efforts_come_from_debug_models(self) -> None:
        payload = json.dumps({"models": [
            {"slug": "gpt-6-astra", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "ultra"}],
             "service_tiers": [{"id": "priority", "name": "Fast", "description": "2x speed, increased usage"}]},
            {"slug": "gpt-5.5", "supported_reasoning_levels": ["low", "high"], "service_tiers": []},
            {"slug": "old-model"},
        ]})
        with patch.object(catalog, "resolveExecutable", return_value="codex.cmd"), \
             patch.object(catalog, "_run", return_value=payload):
            catalog.listModels("codex")
        self.assertEqual(catalog.listEfforts("codex", "gpt-6-astra"), ["low", "medium", "ultra"])
        self.assertEqual(catalog.listEfforts("codex", "gpt-5.5"), ["low", "high"])
        self.assertEqual(catalog.listEfforts("codex", "old-model"), [])
        self.assertEqual(catalog.listEfforts("codex", "unknown"), [])
        # Fast モードは service_tiers の名前が Fast の段。無いモデル・codex 以外は None。
        self.assertEqual(catalog.fastTier("codex", "gpt-6-astra"), "priority")
        self.assertIsNone(catalog.fastTier("codex", "gpt-5.5"))
        self.assertIsNone(catalog.fastTier("claude", "gpt-6-astra"))

    def test_claude_and_agy(self) -> None:
        self.assertEqual(catalog.listEfforts("claude", "haiku"), ["none", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(catalog.listEfforts("agy", "gemini-3.8-flash-high"), [])

    def test_effective_effort_falls_back_to_low_then_first(self) -> None:
        self.assertEqual(catalog.effectiveEffort("high", ["low", "high"]), "high")
        self.assertEqual(catalog.effectiveEffort("ultra", ["low", "high"]), "low")
        self.assertEqual(catalog.effectiveEffort(None, ["medium", "high"]), "medium")
        self.assertIsNone(catalog.effectiveEffort("low", []))


class SessionEffortTests(unittest.TestCase):
    def test_claude_passes_effort_only_when_chosen(self) -> None:
        args = _session(ClaudeSession, "high")._buildArgs()
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertNotIn("--effort", _session(ClaudeSession, None)._buildArgs())

    def test_claude_none_turns_thinking_off(self) -> None:
        args = _session(ClaudeSession, "none")._buildArgs()
        self.assertEqual(args[args.index("--thinking") + 1], "disabled")
        self.assertNotIn("--effort", args)

    def test_codex_turn_uses_the_effort(self) -> None:
        session = _session(CodexSession, "medium")
        session._thread_id = "t"
        self.assertEqual(session._turnMessages("hi")[0]["params"]["effort"], "medium")
        # 選べないときは今までどおり low。
        session = _session(CodexSession, None)
        session._thread_id = "t"
        self.assertEqual(session._turnMessages("hi")[0]["params"]["effort"], "low")

    def test_codex_turn_uses_the_fast_tier_only_when_set(self) -> None:
        session = CodexSession(command_prefix=["cli"], model="m", workspace=".", base_instructions="b",
                               effort="low", service_tier="priority")
        session._thread_id = "t"
        self.assertEqual(session._turnMessages("hi")[0]["params"]["serviceTier"], "priority")
        session = _session(CodexSession, "low")
        session._thread_id = "t"
        self.assertNotIn("serviceTier", session._turnMessages("hi")[0]["params"])


class ClientEffortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # AICliClient は translation_lang が読み込まれている前提 (test_translation_ai_cli_client と同じ)。
        loadTranslationLanguages(path=".", force=True)

    def _client(self, tool: str, model: str, preferred: dict) -> AICliClient:
        client = AICliClient(root_path=None, workspace=".")
        client.tool = tool
        client.model = model
        client.setEffortProvider(lambda t: preferred.get(t))
        return client

    def test_effort_follows_the_saved_choice_per_tool(self) -> None:
        client = self._client("claude", "sonnet", {"claude": "xhigh", "codex": "high"})
        self.assertEqual(client.getEffortList(), catalog.CLAUDE_EFFORTS)
        self.assertEqual(client.getEffort(), "xhigh")

    def test_unsupported_choice_falls_back(self) -> None:
        with patch.dict(catalog._codex_efforts, {"gpt-5.5": ["low", "high"]}, clear=True):
            client = self._client("codex", "gpt-5.5", {"codex": "ultra"})
            self.assertEqual(client.getEffort(), "low")

    def test_agy_has_no_effort(self) -> None:
        client = self._client("agy", "gemini-3.8-flash-high", {})
        self.assertEqual(client.getEffortList(), [])
        self.assertIsNone(client.getEffort())

    def test_fast_mode_only_for_codex_models_that_have_it(self) -> None:
        with patch.dict(catalog._codex_fast_tiers, {"gpt-6-astra": "priority"}, clear=True):
            client = self._client("codex", "gpt-6-astra", {})
            self.assertTrue(client.isFastAvailable())
            self.assertIsNone(client.getServiceTier())  # 関数がまだ無い
            client.setFastProvider(lambda: True)
            self.assertEqual(client.getServiceTier(), "priority")
            client.setFastProvider(lambda: False)
            self.assertIsNone(client.getServiceTier())
            client.model = "gpt-5.5"
            client.setFastProvider(lambda: True)
            self.assertFalse(client.isFastAvailable())
            self.assertIsNone(client.getServiceTier())

    def test_new_session_gets_the_effort_and_restart_drops_the_old_one(self) -> None:
        client = self._client("claude", "haiku", {"claude": "medium"})
        fake_class = MagicMock()
        with patch.dict("models.translation.translation_ai_cli.SESSION_CLASSES", {"claude": fake_class}), \
             patch.object(catalog, "resolveExecutable", return_value="claude.exe"):
            client._ensureSession()
            self.assertEqual(fake_class.call_args.kwargs["effort"], "medium")
            old = client._session
            client.restartSession()
        old.shutdown.assert_called_once()
        self.assertIsNone(client._session)


class ControllerEffortTests(unittest.TestCase):
    def setUp(self) -> None:
        from config import config
        from controller import Controller
        self.config = config
        self.controller = Controller.__new__(Controller)
        self.original = (config.SELECTED_AI_CLI_EFFORTS, config.SELECTED_AI_CLI_TOOL, config.AI_CLI_CODEX_FAST_MODE)

    def tearDown(self) -> None:
        (self.config.SELECTED_AI_CLI_EFFORTS, self.config.SELECTED_AI_CLI_TOOL,
         self.config.AI_CLI_CODEX_FAST_MODE) = self.original

    def test_fast_mode_toggle_restarts_the_session_only_when_it_changes(self) -> None:
        self.config.AI_CLI_CODEX_FAST_MODE = False
        with patch("controller.model") as model:
            self.assertEqual(self.controller.setEnableAiCliFastMode(), {"status": 200, "result": True})
            self.assertEqual(self.controller.setEnableAiCliFastMode(), {"status": 200, "result": True})
            self.assertEqual(self.controller.setDisableAiCliFastMode(), {"status": 200, "result": False})
        self.assertEqual(model.restartTranslatorAiCliSession.call_count, 2)

    def test_set_saves_per_tool_and_restarts_the_session(self) -> None:
        self.config.SELECTED_AI_CLI_TOOL = "claude"
        with patch("controller.model") as model:
            model.getTranslatorAiCliEffortList.return_value = catalog.CLAUDE_EFFORTS
            model.getTranslatorAiCliEffort.return_value = "high"
            result = self.controller.setSelectedAiCliEffort("high")
        self.assertEqual(result, {"status": 200, "result": "high"})
        self.assertEqual(self.config.SELECTED_AI_CLI_EFFORTS["claude"], "high")
        model.restartTranslatorAiCliSession.assert_called_once()

    def test_set_rejects_an_effort_the_model_does_not_have(self) -> None:
        self.config.SELECTED_AI_CLI_TOOL = "claude"
        before = dict(self.config.SELECTED_AI_CLI_EFFORTS or {})
        with patch("controller.model") as model:
            model.getTranslatorAiCliEffortList.return_value = ["low", "high"]
            model.getTranslatorAiCliEffort.return_value = "low"
            result = self.controller.setSelectedAiCliEffort("ultra")
        self.assertEqual(result["status"], 400)
        self.assertEqual(dict(self.config.SELECTED_AI_CLI_EFFORTS or {}), before)
        model.restartTranslatorAiCliSession.assert_not_called()


if __name__ == "__main__":
    unittest.main()
