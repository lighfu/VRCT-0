import requests

try:
    from .translation_languages import translation_lang
    from .translation_utils import loadTranslatePromptConfig
    from .translation_llm_common import OpenAIChat, buildSystemPrompt
except Exception:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.abspath(__file__)))
    from translation_languages import translation_lang, loadTranslationLanguages
    from translation_utils import loadTranslatePromptConfig
    from translation_llm_common import OpenAIChat, buildSystemPrompt
    translation_lang = loadTranslationLanguages(path=".", force=True)

def _authentication_check(base_url: str | None = None) -> bool:
    """Check if the provided API key is valid by attempting to list models.
    """
    try:
        response = requests.get(f"{base_url}/models", timeout=0.2)
        if response.status_code == 200:
            return True
        else:
            return False
    except Exception:
        return False

def _get_available_text_models(base_url: str | None = None) -> list[str]:
    """Extract the list of available text models from the LM Studio.
    """
    try:
        response = requests.get(f"{base_url}/models", timeout=0.2)
        models = response.json()["data"]
    except Exception:
        models = []

    allowed_models = []
    for model in models:
        allowed_models.append(model["id"])

    allowed_models.sort()
    return allowed_models

class LMStudioClient:
    """LM Studio Translation simple wrapper.
    prompt/translation_lmstudio.yml から system_prompt / supported_languages を読み込む。
    """
    def __init__(self, base_url: str | None = None, root_path: str = None):
        self.api_key = "lmstudio"
        self.model = None
        self.base_url = base_url  # None の場合は公式エンドポイント

        prompt_config = loadTranslatePromptConfig(root_path, "translation_lmstudio.yml")
        self.supported_languages = list(translation_lang["LMStudio"]["source"].keys())
        self.prompt_template = prompt_config["system_prompt"]
        # history config (optional)
        self.history_cfg = prompt_config.get("history", {
            "use_history": False,
            "sources": [],
            "max_messages": 0,
            "max_chars": 0,
            "header_template": "",
            "item_template": "[{source}] {role}: {text}",
        })
        self._context_history: list[dict] = []

        self.openai_llm = None

    def getBaseURL(self) -> str | None:
        return self.base_url

    def setBaseURL(self, base_url: str | None) -> None:
        result = _authentication_check(base_url=base_url)
        if result:
            self.base_url = base_url
        return result

    def getModelList(self) -> list[str]:
        return _get_available_text_models(base_url=self.base_url) if self.base_url else []

    def getModel(self) -> str:
        return self.model

    def setModel(self, model: str) -> bool:
        if model in self.getModelList():
            self.model = model
            return True
        else:
            return False

    def updateClient(self) -> None:
        self.openai_llm = OpenAIChat(base_url=self.base_url, api_key=self.api_key, model=self.model)

    def setContextHistory(self, history_items: list[dict]) -> None:
        """Set recent conversation history for prompt injection.

        Each item should be a dict containing:
        - source: "chat" | "mic" | "speaker"
        - text: message string
        - timestamp: ISO format datetime string
        """
        self._context_history = history_items or []

    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        system_prompt = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        return self.openai_llm.complete(messages)

if __name__ == "__main__":
    client = LMStudioClient(base_url="http://127.0.0.1:1234/v1")
    models = client.getModelList()
    if models:
        print("Available models:", models)
        model = input("Select a model: ")
        client.setModel(model)
        client.updateClient()
        print(client.translate("こんにちは世界", "Japanese", "English"))