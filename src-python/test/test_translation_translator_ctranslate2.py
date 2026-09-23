import unittest
from unittest.mock import MagicMock, patch

from models.translation import translation_translator as tt_module
from models.translation.translation_translator import Translator


class FakeHypothesis:
    def __init__(self, tokens: list[str]) -> None:
        self.hypotheses = [tokens]


class FakeTokenizer:
    def __init__(self, family: str) -> None:
        self.family = family

    def _lang(self, lang: str) -> str:
        return f"__{lang}__" if self.family == "m2m100" else lang

    def encode(self, text: str, source_lang: str) -> list[str]:
        return [self._lang(source_lang)] + list(text) + ["</s>"]

    def targetPrefix(self, target_lang: str) -> list[str]:
        return [self._lang(target_lang)]

    def decode(self, tokens: list[str]) -> str:
        return "".join(tokens)


class TestTranslateCTranslate2Dispatch(unittest.TestCase):
    def _translator(self, family: str) -> Translator:
        translator = Translator()
        translator.is_loaded_ctranslate2_model = True
        translator.ctranslate2_tokenizer = FakeTokenizer(family)
        translator.ctranslate2_translator = MagicMock()
        translator.ctranslate2_translator.translate_batch.return_value = [FakeHypothesis(["_prefix_", "h", "i"])]
        return translator

    def test_m2m100_uses_wrapped_language_token_as_prefix(self) -> None:
        translator = self._translator("m2m100")
        result = translator.translateCTranslate2("hi", "ja", "en", "m2m100_418M-ct2-int8")
        args, kwargs = translator.ctranslate2_translator.translate_batch.call_args
        self.assertEqual(kwargs["target_prefix"], [["__en__"]])
        self.assertEqual(args[0], [["__ja__", "h", "i", "</s>"]])
        self.assertEqual(result, "hi")

    def test_nllb_600m_uses_raw_language_code_as_prefix(self) -> None:
        translator = self._translator("nllb")
        translator.translateCTranslate2("hi", "jpn_Jpan", "eng_Latn", "nllb-200-distilled-600M-ct2-int8")
        _, kwargs = translator.ctranslate2_translator.translate_batch.call_args
        self.assertEqual(kwargs["target_prefix"], [["eng_Latn"]])

    def test_unknown_weight_type_returns_false(self) -> None:
        translator = self._translator("m2m100")
        self.assertFalse(translator.translateCTranslate2("hi", "ja", "en", "unknown-weight"))
        translator.ctranslate2_translator.translate_batch.assert_not_called()


class TestChangeCTranslate2Model(unittest.TestCase):
    def test_change_model_leaves_unloaded_when_tokenizer_fails(self) -> None:
        # オフラインでトークナイザのキャッシュも無いと loadCT2Tokenizer は例外を投げる。
        # 呼び出し元 (model.py) のスレッドを殺さず、未ロードのまま残ること。
        translator = Translator()
        fake_ct2 = MagicMock()
        with patch.object(tt_module, "ctranslate2", fake_ct2), \
             patch.object(tt_module, "loadCT2Tokenizer", side_effect=OSError("offline")):
            translator.changeCTranslate2Model(path=".", model_type="m2m100_418M-ct2-int8")
        self.assertFalse(translator.isLoadedCTranslate2Model())
        # Minor 5: don't leave a loaded ctranslate2_translator pointing at a model
        # whose tokenizer never loaded.
        self.assertIsNone(translator.ctranslate2_translator)

    def test_change_model_is_loaded_when_fallback_tokenizer_path_succeeds(self) -> None:
        # First loadCT2Tokenizer attempt (weights/ctranslate2/.../tokenizer under `path`)
        # fails, but the fallback attempt (relative ./weights/... path) succeeds.
        translator = Translator()
        fake_ct2 = MagicMock()
        fake_tokenizer = FakeTokenizer("m2m100")
        with patch.object(tt_module, "ctranslate2", fake_ct2), \
             patch.object(
                 tt_module,
                 "loadCT2Tokenizer",
                 side_effect=[OSError("offline"), fake_tokenizer],
             ):
            translator.changeCTranslate2Model(path=".", model_type="m2m100_418M-ct2-int8")
        self.assertTrue(translator.isLoadedCTranslate2Model())
        self.assertIsNotNone(translator.ctranslate2_translator)
        self.assertIs(translator.ctranslate2_tokenizer, fake_tokenizer)


if __name__ == "__main__":
    unittest.main()
