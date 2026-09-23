import unittest
from unittest.mock import MagicMock, patch

from models.translation import translation_ai_cli as mod
from models.translation.translation_ai_cli import AICliClient


class AICliClientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(mod.catalog, "detectInstalledTools", return_value=["codex", "claude"])
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(mod.catalog, "resolveExecutable", side_effect=lambda t: f"C:/bin/{t}.exe" if t in ("codex", "claude") else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(mod.catalog, "listModels", side_effect=lambda t: {"claude": ["haiku", "sonnet"], "codex": ["gpt-5.5"]}.get(t, []))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = AICliClient(workspace="C:/tmp/ws")
        self.addCleanup(self.client.close)

    def test_set_tool_accepts_only_installed(self):
        self.assertTrue(self.client.setTool("claude"))
        self.assertFalse(self.client.setTool("agy"))
        self.assertEqual(self.client.getTool(), "claude")

    def test_model_must_be_in_the_tool_list(self):
        self.client.setTool("claude")
        self.assertEqual(self.client.getModelList(), ["haiku", "sonnet"])
        self.assertTrue(self.client.setModel("sonnet"))
        self.assertFalse(self.client.setModel("gpt-5.5"))
        self.assertEqual(self.client.getModel(), "sonnet")

    def test_authentication_check_needs_an_installed_tool(self):
        self.assertFalse(self.client.authenticationCheck())
        self.client.setTool("codex")
        self.assertTrue(self.client.authenticationCheck())

    def test_translate_builds_prompt_and_uses_tool_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        fake_session = MagicMock()
        fake_session.translate.return_value = "Hello"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=fake_session)}) as classes:
            result = self.client.translate("こんにちは", "Japanese", "English")
            self.assertEqual(result, "Hello")
            kwargs = classes["claude"].call_args.kwargs
            self.assertEqual(kwargs["command_prefix"], ["C:/bin/claude.exe"])
            self.assertEqual(kwargs["model"], "haiku")
            self.assertEqual(kwargs["workspace"], "C:/tmp/ws")
            prompt = fake_session.translate.call_args.args[0]
            self.assertIn("from Japanese to English", prompt)
            self.assertTrue(prompt.endswith("Text to translate:\nこんにちは"))

    def test_set_tool_closes_previous_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        first = MagicMock()
        first.translate.return_value = "x"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=first)}):
            self.client.translate("a", "Japanese", "English")
        self.client.setTool("codex")
        first.shutdown.assert_called_once()

    def test_set_model_to_a_different_value_closes_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        first = MagicMock()
        first.translate.return_value = "x"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=first)}):
            self.client.translate("a", "Japanese", "English")
        self.client.setModel("sonnet")
        first.shutdown.assert_called_once()

    def test_translate_without_model_raises(self):
        self.client.setTool("claude")
        with self.assertRaises(mod.AiCliError):
            self.client.translate("a", "Japanese", "English")

    def test_update_client_warms_up_in_background(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        session = MagicMock()
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=session)}), \
             patch.object(mod.threading, "Thread") as mock_thread:
            self.client.updateClient()
            target = mock_thread.call_args.kwargs["target"]
            target()
        session.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
