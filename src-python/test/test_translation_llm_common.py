import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from models.translation import translation_llm_common as common

_TEMPLATE = "Translate {input_lang} to {output_lang}. Langs: {supported_languages}"
_HISTORY_CFG = {
    "use_history": True,
    "sources": ["chat", "mic"],
    "max_messages": 2,
    "max_chars": 0,
    "header_template": "History ({max_messages}):\n{history}",
    "item_template": "[{timestamp}][{source}] {text}",
}


class BuildSystemPromptTests(unittest.TestCase):
    def test_without_history_only_formats_the_template(self) -> None:
        prompt = common.buildSystemPrompt(_TEMPLATE, ["ja", "en"], "Japanese", "English", {"use_history": False}, [])
        self.assertEqual(prompt, "Translate Japanese to English. Langs: ['ja', 'en']")

    def test_history_is_filtered_by_source_and_limited_to_newest(self) -> None:
        history = [
            {"source": "chat", "text": "old", "timestamp": "2026-09-23T10:00:00"},
            {"source": "speaker", "text": "skip", "timestamp": "2026-09-23T10:01:00"},
            {"source": "mic", "text": "mid", "timestamp": "2026-09-23T10:02:00"},
            {"source": "chat", "text": "new", "timestamp": "2026-09-23T10:03:00"},
        ]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "Japanese", "English", _HISTORY_CFG, history)
        self.assertTrue(prompt.endswith("History (2):\n[10:02][mic] mid\n[10:03][chat] new"))
        self.assertNotIn("old", prompt)
        self.assertNotIn("skip", prompt)

    def test_invalid_timestamp_becomes_empty(self) -> None:
        history = [{"source": "chat", "text": "hi", "timestamp": "broken"}]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "a", "b", _HISTORY_CFG, history)
        self.assertTrue(prompt.endswith("[][chat] hi"))

    def test_history_is_truncated_from_the_front_by_max_chars(self) -> None:
        cfg = dict(_HISTORY_CFG, max_chars=5, header_template="{history}")
        history = [{"source": "chat", "text": "abcdefghij"}]
        prompt = common.buildSystemPrompt(_TEMPLATE, [], "a", "b", cfg, history)
        self.assertTrue(prompt.endswith("\n\nfghij"))


class ExtractTextTests(unittest.TestCase):
    def test_string_is_stripped(self) -> None:
        self.assertEqual(common.extractText("  hi \n"), "hi")

    def test_list_parts_are_concatenated(self) -> None:
        self.assertEqual(common.extractText(["a", {"content": "b"}, {"type": "x"}, 3]), "ab")

    def test_none_becomes_empty(self) -> None:
        self.assertEqual(common.extractText(None), "")


class OpenAIChatTests(unittest.TestCase):
    @patch("openai.OpenAI")
    def test_complete_sends_messages_and_returns_stripped_text(self, mock_openai) -> None:
        client = mock_openai.return_value
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" hello "))]
        )
        chat = common.OpenAIChat(base_url="https://example/v1", api_key="k", model="m")
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

        self.assertEqual(chat.complete(messages), "hello")
        mock_openai.assert_called_once_with(api_key="k", base_url="https://example/v1")
        client.chat.completions.create.assert_called_once_with(model="m", messages=messages, stream=False)

    @patch("openai.OpenAI")
    def test_complete_returns_empty_string_for_none_content(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
        chat = common.OpenAIChat(base_url=None, api_key="k", model="m")
        self.assertEqual(chat.complete([{"role": "user", "content": "u"}]), "")


class GeminiChatTests(unittest.TestCase):
    @patch("google.genai.Client")
    def test_complete_passes_system_prompt_as_system_instruction(self, mock_client_class) -> None:
        client = mock_client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(text=" konnichiwa ")
        chat = common.GeminiChat(api_key="k", model="gemini-x")
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]

        self.assertEqual(chat.complete(messages), "konnichiwa")
        mock_client_class.assert_called_once_with(api_key="k")
        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-x")
        self.assertEqual(kwargs["contents"], ["hello"])
        self.assertEqual(kwargs["config"].system_instruction, "sys")
        self.assertEqual(kwargs["config"].temperature, common.GeminiChat._DEFAULT_TEMPERATURE)
        self.assertEqual(
            kwargs["config"].http_options.retry_options.attempts,
            common.GeminiChat._DEFAULT_MAX_RETRIES,
        )


if __name__ == "__main__":
    unittest.main()
