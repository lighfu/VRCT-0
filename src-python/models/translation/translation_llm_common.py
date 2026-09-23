"""LLM 翻訳クライアント (OpenAI / Groq / OpenRouter / LM Studio / PLaMo /
Ollama / Gemini) の共通処理。

以前は各クライアントが langchain の ChatOpenAI / ChatOllama /
ChatGoogleGenerativeAI を持ち、system プロンプトの組み立てと応答の
取り出しを 1 つずつ複製していた。使っていたのは .invoke(messages) だけ
なので、openai SDK と google-genai を直接呼ぶ形にまとめた (A-1, 2026-09-23)。
"""

from datetime import datetime
from typing import Any


def buildSystemPrompt(
    prompt_template: str,
    supported_languages: list[str],
    input_lang: str,
    output_lang: str,
    history_cfg: dict,
    context_history: list[dict],
) -> str:
    system_prompt = prompt_template.format(
        supported_languages=supported_languages,
        input_lang=input_lang,
        output_lang=output_lang,
    )
    if not history_cfg.get("use_history"):
        return system_prompt

    allowed_sources = set(history_cfg.get("sources", []))
    max_messages = int(history_cfg.get("max_messages", 0))
    max_chars = int(history_cfg.get("max_chars", 0))
    item_tmpl = history_cfg.get("item_template", "[{source}] {role}: {text}")
    header_tmpl = history_cfg.get("header_template", "{history}")

    filtered = [h for h in context_history if h.get("source") in allowed_sources]
    recent = filtered[-max_messages:] if max_messages > 0 else filtered
    formatted_items = []
    for h in recent:
        # トークン節約のため時刻は HH:MM だけにする
        timestamp_str = ""
        if "timestamp" in h:
            try:
                timestamp_str = datetime.fromisoformat(h["timestamp"]).strftime("%H:%M")
            except (TypeError, ValueError):
                timestamp_str = ""
        formatted_items.append(
            item_tmpl.format(timestamp=timestamp_str, source=h.get("source", ""), text=h.get("text", ""))
        )
    history_blob = "\n".join(formatted_items).strip()
    if max_chars and len(history_blob) > max_chars:
        history_blob = history_blob[-max_chars:]
    history_header = header_tmpl.format(max_messages=max_messages, history=history_blob)
    if history_header:
        system_prompt = f"{system_prompt}\n\n{history_header}"
    return system_prompt


def extractText(content: Any) -> str:
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text += part
            elif isinstance(part, dict) and isinstance(part.get("content"), str):
                text += part["content"]
    return text.strip()


class OpenAIChat:
    """OpenAI 互換の Chat Completions エンドポイントを 1 往復だけ呼ぶ。"""

    def __init__(self, base_url: str | None, api_key: str, model: str) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def complete(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(model=self._model, messages=messages, stream=False)
        return extractText(response.choices[0].message.content)


class GeminiChat:
    """Gemini の generate_content を 1 往復だけ呼ぶ。system メッセージは system_instruction に渡す。"""

    # langchain-google-genai==2.1.10's ChatGoogleGenerativeAI (_BaseGoogleGenerativeAI in
    # langchain_google_genai/_common.py) used to apply these defaults for us:
    # temperature=0.7, max_retries=6 (verified by downloading the wheel with
    # `pip download langchain-google-genai==2.1.10 --no-deps` and reading its source; not
    # installed, since we removed the langchain dependency entirely). Now that we call the
    # google-genai SDK directly, reproduce temperature via GenerateContentConfig. For
    # retries, google-genai 1.45.0's HttpRetryOptions(attempts=...) is a cheap equivalent
    # to langchain's max_retries, so we set it to the same value (6) via HttpOptions.
    _DEFAULT_TEMPERATURE = 0.7
    _DEFAULT_MAX_RETRIES = 6

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete(self, messages: list[dict]) -> str:
        from google.genai import types
        system_instruction = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [m["content"] for m in messages if m["role"] != "system"]
        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction or None,
                temperature=self._DEFAULT_TEMPERATURE,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=self._DEFAULT_MAX_RETRIES),
                ),
            ),
        )
        return extractText(response.text)
