OpenAI = None  # 使うときに _openai() が読み込む (起動時間の短縮, A-1)


def _openai():
    global OpenAI
    if OpenAI is None:
        from openai import OpenAI as _cls
        OpenAI = _cls
    return OpenAI


try:
    from .translation_languages import translation_lang
    from .translation_utils import loadTranslatePromptConfig
    from .translation_llm_common import OpenAIChat, buildSystemPrompt
except Exception:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from translation_languages import translation_lang, loadTranslationLanguages
    from translation_utils import loadTranslatePromptConfig
    from translation_llm_common import OpenAIChat, buildSystemPrompt
    translation_lang = loadTranslationLanguages(path=".", force=True)

def _authentication_check(api_key: str) -> bool:
    """Check if the provided API key is valid by attempting to list models.
    """
    try:
        client = _openai()(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        client.models.list()
        return True
    except Exception:
        return False

def _get_available_text_models(api_key: str) -> list[str]:
    """Extract only Groq models suitable for translation and chat applications.
    """
    client = _openai()(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    res = client.models.list()
    allowed_models = []

    for model in res.data:
        model_id = model.id

        # 除外対象のキーワード
        exclude_keywords = [
            "whisper",       # 音声認識
            "embedding",     # 埋め込み
            "image",         # 画像生成
            "tts",           # 音声合成
            "audio",         # 音声系
            "search",        # 検索補助モデル
            "transcribe",    # 音声→文字起こし
            "diarize",       # 話者分離
            "vision"         # 画像入力系
        ]

        # 除外キーワードが含まれているモデルをスキップ
        if any(kw in model_id.lower() for kw in exclude_keywords):
            continue

        # テキスト処理用モデルのみ対象
        allowed_models.append(model_id)

    allowed_models.sort()
    return allowed_models

class GroqClient:
    """Groq API Translation wrapper using OpenAI-compatible endpoint.
    
    Groq provides a fast LLM inference platform with an OpenAI-compatible API.
    The API endpoint: https://api.groq.com/openai/v1
    """
    def __init__(self, root_path: str = None):
        self.api_key = None
        self.model = None
        self.base_url = "https://api.groq.com/openai/v1"

        prompt_config = loadTranslatePromptConfig(root_path, "translation_groq.yml")
        self.supported_languages = list(translation_lang["Groq_API"]["source"].keys())
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

        self.groq_llm = None

    def getModelList(self) -> list[str]:
        return _get_available_text_models(self.api_key) if self.api_key else []

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
        self.groq_llm = OpenAIChat(base_url=self.base_url, api_key=self.api_key, model=self.model)

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
        return self.groq_llm.complete(messages)

if __name__ == "__main__":
    AUTH_KEY = "GROQ_API_KEY"
    client = GroqClient()
    client.setAuthKey(AUTH_KEY)
    models = client.getModelList()
    if models:
        print("Available models:", models)
        model = input("Select a model: ")
        client.setModel(model)
        client.updateClient()
        print(client.translate("こんにちは世界", "Japanese", "English"))
