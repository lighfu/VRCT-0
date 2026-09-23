import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from models.translation import translation_ai_cli as mod
from models.translation.translation_ai_cli import AICliClient
from models.translation.translation_languages import loadTranslationLanguages


class AICliClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # translation_ai_cli は translation_lang が既に読み込まれている前提
        # (通常は config.Config() 初期化時に読み込まれる)。単体テストではここで読む。
        loadTranslationLanguages(path=".", force=True)

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
            self.assertEqual(kwargs["base_instructions"], mod.BASE_INSTRUCTIONS)
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

    def test_set_model_to_the_same_value_does_not_close_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        first = MagicMock()
        first.translate.return_value = "x"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=first)}):
            self.client.translate("a", "Japanese", "English")
            self.assertTrue(self.client.setModel("haiku"))
            first.shutdown.assert_not_called()

    def test_set_tool_resets_model_to_none(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        self.client.setTool("codex")
        self.assertIsNone(self.client.getModel())

    def test_set_model_swaps_state_and_session_atomically(self):
        """setModel() の状態更新とセッションの入れ替えは同じ臨界区間で行われ、
        古いセッションの shutdown() (時間がかかる) が終わるのを待たずに、
        次の _ensureSession() は既に新しいモデルでセッションを作る。"""
        self.client.setTool("claude")
        self.client.setModel("haiku")

        release = threading.Event()
        old_session = MagicMock()
        old_session.shutdown.side_effect = lambda: release.wait(5)
        new_session = MagicMock()
        sessions_by_model = {"haiku": old_session, "sonnet": new_session}

        def makeSession(**kwargs):
            return sessions_by_model[kwargs["model"]]

        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(side_effect=makeSession)}):
            # 現在のセッション (haiku) を作らせておく。
            self.assertIs(self.client._ensureSession(), old_session)

            setModelThread = threading.Thread(target=self.client.setModel, args=("sonnet",))
            setModelThread.start()
            try:
                deadline = time.monotonic() + 2
                while not old_session.shutdown.called and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertTrue(old_session.shutdown.called, "setModel が old.shutdown() を呼んでいない")

                # old.shutdown() がまだブロックしている間に、次のセッション取得は
                # 既に新しいモデルで作られていなければならない。
                session = self.client._ensureSession()
                self.assertIs(session, new_session)
                self.assertEqual(self.client.getModel(), "sonnet")
            finally:
                release.set()
                setModelThread.join(5)

    def test_shutdown_is_terminal(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        self.client.shutdown()
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock()}) as classes:
            with self.assertRaises(mod.AiCliError):
                self.client.translate("a", "Japanese", "English")
            classes["claude"].assert_not_called()

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
