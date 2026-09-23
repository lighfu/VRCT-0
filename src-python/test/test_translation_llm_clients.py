"""LLM 翻訳クライアントが共通モジュール経由で呼び出すことの確認。"""

import importlib
import unittest
from unittest.mock import patch

# (モジュール名, クラス名, LLM 属性名, 期待する base_url, 初期化で渡す kwargs)
_CLIENTS = [
    ("translation_openai", "OpenAIClient", "openai_llm", None, {}),
    ("translation_groq", "GroqClient", "groq_llm", "https://api.groq.com/openai/v1", {}),
    ("translation_openrouter", "OpenRouterClient", "openrouter_llm", "https://openrouter.ai/api/v1", {}),
    ("translation_plamo", "PlamoClient", "plamo_llm", "https://api.platform.preferredai.jp/v1", {}),
    ("translation_lmstudio", "LMStudioClient", "openai_llm", "http://127.0.0.1:1234/v1", {"base_url": "http://127.0.0.1:1234/v1"}),
    ("translation_ollama", "OllamaClient", "openai_llm", "http://localhost:11434/v1", {}),
]


class OpenAICompatibleClientTests(unittest.TestCase):
    def _makeClient(self, module_name, class_name, kwargs):
        module = importlib.import_module(f"models.translation.{module_name}")
        client = getattr(module, class_name)(**kwargs)
        client.api_key = client.api_key or "key"
        client.model = "model-x"
        return module, client

    def test_update_client_builds_openai_chat_with_provider_base_url(self) -> None:
        for module_name, class_name, attr, base_url, kwargs in _CLIENTS:
            with self.subTest(client=class_name):
                module, client = self._makeClient(module_name, class_name, kwargs)
                with patch.object(module, "OpenAIChat") as mock_chat:
                    client.updateClient()
                _, call_kwargs = mock_chat.call_args
                self.assertEqual(call_kwargs["base_url"], base_url)
                self.assertEqual(call_kwargs["model"], "model-x")
                self.assertIs(getattr(client, attr), mock_chat.return_value)

    def test_translate_sends_system_and_user_messages(self) -> None:
        for module_name, class_name, attr, _, kwargs in _CLIENTS:
            with self.subTest(client=class_name):
                module, client = self._makeClient(module_name, class_name, kwargs)
                with patch.object(module, "OpenAIChat") as mock_chat:
                    mock_chat.return_value.complete.return_value = "translated"
                    client.updateClient()
                    result = client.translate("こんにちは", "Japanese", "English")
                self.assertEqual(result, "translated")
                messages = mock_chat.return_value.complete.call_args.args[0]
                self.assertEqual([m["role"] for m in messages], ["system", "user"])
                self.assertIn("English", messages[0]["content"])
                self.assertEqual(messages[1]["content"], "こんにちは")


class GeminiClientTests(unittest.TestCase):
    def test_translate_goes_through_gemini_chat(self) -> None:
        from models.translation import translation_gemini
        client = translation_gemini.GeminiClient()
        client.api_key = "key"
        client.model = "gemini-x"
        with patch.object(translation_gemini, "GeminiChat") as mock_chat:
            mock_chat.return_value.complete.return_value = "translated"
            client.updateClient()
            result = client.translate("こんにちは", "Japanese", "English")
        mock_chat.assert_called_once_with(api_key="key", model="gemini-x")
        self.assertEqual(result, "translated")
        messages = mock_chat.return_value.complete.call_args.args[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])


if __name__ == "__main__":
    unittest.main()
