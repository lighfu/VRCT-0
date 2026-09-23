genai = None  # 使うときに _genai() が読み込む (起動時間の短縮, A-1)


def _genai():
    global genai
    if genai is None:
        from google import genai as _module
        genai = _module
    return genai


try:
    from .translation_languages import translation_lang
    from .translation_utils import loadTranslatePromptConfig
    from .translation_llm_common import GeminiChat, buildSystemPrompt
except Exception:
    import sys
    from os import path as os_path
    print(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from translation_languages import translation_lang
    from translation_utils import loadTranslatePromptConfig
    from translation_llm_common import GeminiChat, buildSystemPrompt

def _authentication_check(api_key: str) -> bool:
    """Check if the provided API key is valid by attempting to list models.
    """
    try:
        client = _genai().Client(api_key=api_key)
        client.models.list()
        return True
    except Exception:
        return False

def _get_available_text_models(api_key: str) -> list[str]:
    """Extract only Gemini models suitable for translation and chat applications
    """
    client = _genai().Client(api_key=api_key)
    res = client.models.list()
    allowed_models = []

    # 除外対象のキーワード
    exclude_keywords = [
        "audio",
        "image",
        "veo",
        "tts",
        "robotics",
        "computer-use"
    ]
    for model in res:
        model_id = model.name
        if ("gemini" in model_id.lower() or "gemma" in model_id.lower()) and "generateContent" in model.supported_actions:
            if any(x in model_id for x in exclude_keywords):
                continue
            allowed_models.append(model_id.replace("models/", ""))
    allowed_models.sort()
    return allowed_models

class GeminiClient:
    def __init__(self, root_path: str = None):
        self.api_key = None
        self.model = None

        # プロンプト設定をYAMLファイルから読み込む
        prompt_config = loadTranslatePromptConfig(root_path, "translation_gemini.yml")
        self.supported_languages = list(translation_lang["Gemini_API"]["source"].keys())
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

        self.gemini_llm = None

    def getModelList(self) -> list[str]:
        return _get_available_text_models(self.api_key)

    def getAuthKey(self) -> str:
        return self.api_key

    def setAuthKey(self, api_key: str) -> bool:
        result = _authentication_check(api_key)
        if result:
            self.api_key = api_key
        return result

    def getModel(self) -> str:
        return self.model

    def setModel(self, model: str) -> bool:
        if model in self.getModelList():
            self.model = model
            return True
        else:
            return False

    def updateClient(self) -> None:
        self.gemini_llm = GeminiChat(api_key=self.api_key, model=self.model)

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
        return self.gemini_llm.complete(messages)

if __name__ == "__main__":
    AUTH_KEY = "AUTH_KEY"
    client = GeminiClient()
    client.setAuthKey(AUTH_KEY)
    models = client.getModelList()
    if models:
        print("Available models:", models)
        model = input("Select a model: ")
        client.setModel(model)
        client.updateClient()
        print(client.translate("こんにちは世界", "Japanese", "English"))