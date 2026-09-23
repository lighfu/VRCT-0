import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sentencepiece

from models.translation import translation_ct2_tokenizer as ct2tok

_CORPUS = [
    "hello world this is a small test corpus",
    "the quick brown fox jumps over the lazy dog",
    "translation models need tokenizers",
    "sentence piece splits words into pieces",
] * 20


def _trainSentencePiece(directory: str) -> str:
    corpus_path = os.path.join(directory, "corpus.txt")
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.write("\n".join(_CORPUS))
    prefix = os.path.join(directory, "sp")
    sentencepiece.SentencePieceTrainer.train(
        input=corpus_path, model_prefix=prefix, vocab_size=60, model_type="bpe",
        hard_vocab_limit=False, bos_id=-1, eos_id=-1, unk_id=0,
    )
    return prefix + ".model"


class CT2TokenizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.sp_path = _trainSentencePiece(cls._tmp.name)
        cls.sp = sentencepiece.SentencePieceProcessor(model_file=cls.sp_path)
        pieces = [cls.sp.id_to_piece(i) for i in range(cls.sp.get_piece_size())]
        cls.m2m_vocab_path = os.path.join(cls._tmp.name, "vocab.json")
        with open(cls.m2m_vocab_path, "w", encoding="utf-8") as f:
            json.dump({p: i for i, p in enumerate(pieces)}, f)
        cls.nllb_vocab_path = os.path.join(cls._tmp.name, "tokenizer.json")
        with open(cls.nllb_vocab_path, "w", encoding="utf-8") as f:
            json.dump({"model": {"vocab": {p: i for i, p in enumerate(pieces)}}}, f)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_family_is_derived_from_weight_type(self) -> None:
        self.assertEqual(ct2tok.tokenizerFamily("m2m100_418M-ct2-int8"), ct2tok.M2M100)
        self.assertEqual(ct2tok.tokenizerFamily("nllb-200-3.3B-ct2-int8"), ct2tok.NLLB)
        with self.assertRaises(ValueError):
            ct2tok.tokenizerFamily("unknown")

    def test_m2m100_wraps_pieces_with_language_token_and_eos(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, self.m2m_vocab_path)
        tokens = tokenizer.encode("hello world", "ja")
        self.assertEqual(tokens[0], "__ja__")
        self.assertEqual(tokens[-1], "</s>")
        self.assertEqual(tokens[1:-1], self.sp.encode("hello world", out_type=str))
        self.assertEqual(tokenizer.targetPrefix("en"), ["__en__"])

    def test_nllb_uses_raw_language_code(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.NLLB, self.sp_path, self.nllb_vocab_path)
        tokens = tokenizer.encode("hello", "jpn_Jpan")
        self.assertEqual(tokens[0], "jpn_Jpan")
        self.assertEqual(tokens[-1], "</s>")
        self.assertEqual(tokenizer.targetPrefix("eng_Latn"), ["eng_Latn"])

    def test_pieces_missing_from_vocab_become_unk(self) -> None:
        vocab_path = os.path.join(self._tmp.name, "tiny_vocab.json")
        with open(vocab_path, "w", encoding="utf-8") as f:
            json.dump({"<unk>": 0}, f)
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, vocab_path)
        tokens = tokenizer.encode("hello", "en")
        self.assertTrue(all(t == "<unk>" for t in tokens[1:-1]))

    def test_decode_drops_special_tokens_and_round_trips(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, self.m2m_vocab_path)
        pieces = self.sp.encode("the lazy dog", out_type=str)
        self.assertEqual(tokenizer.decode(pieces + ["</s>"]), "the lazy dog")

    def test_vocab_with_literal_model_key_is_treated_as_flat_vocab(self) -> None:
        # Real m2m100 vocab.json contains a literal token "model" (id 112482),
        # so disambiguating flat-vs-nested vocab by `"model" in data` breaks;
        # it must check whether data["model"] is itself a dict.
        vocab_path = os.path.join(self._tmp.name, "vocab_with_model_token.json")
        with open(vocab_path, "w", encoding="utf-8") as f:
            json.dump({"model": 5, "<unk>": 0, "hello": 1}, f)
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, vocab_path)
        self.assertIn("model", tokenizer._vocab)
        self.assertIn("hello", tokenizer._vocab)


class CleanUpTokenizationSpacesTests(unittest.TestCase):
    def test_strips_space_before_punctuation_and_contractions(self) -> None:
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("comment ça va ?"), "comment ça va?")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("hello , world ."), "hello, world.")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("wait !"), "wait!")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("do n't"), "don't")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("I 'm"), "I'm")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("it 's"), "it's")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("I 've"), "I've")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("we 're"), "we're")
        self.assertEqual(ct2tok._cleanUpTokenizationSpaces("say ' hi '"), "say'hi '")


class DecodeCleanUpTests(unittest.TestCase):
    """CT2Tokenizer.decode() applies clean_up_tokenization for both families.

    NOTE: an earlier plan assumed NLLB's parity already passed without this
    cleanup and asked for M2M100-only handling. Verified empirically against
    the real facebook/nllb-200-distilled-600M tokenizer that this is not the
    case: NLLB's tokenizer_config.json also omits
    clean_up_tokenization_spaces, which resolves to the transformers default
    (True) exactly like M2M100, and NLLB's own decode() strips the same
    " ?"/" !" style spacing. So the cleanup is applied to both families here.

    `sentencepiece.decode_pieces` only recognizes pieces the underlying SP
    model actually trained (a synthetic " ?" piece is echoed back literally,
    not space-normalized), so these tests stub `_sp.decode_pieces` directly
    to isolate `_cleanUpTokenizationSpaces` being applied by `decode()`,
    independent of sentencepiece's own piece-joining behavior.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.sp_path = _trainSentencePiece(cls._tmp.name)
        cls.vocab_path = os.path.join(cls._tmp.name, "vocab.json")
        with open(cls.vocab_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_m2m100_decode_strips_space_before_question_mark(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.M2M100, self.sp_path, self.vocab_path)
        with patch.object(tokenizer._sp, "decode_pieces", return_value="va ?"):
            self.assertEqual(tokenizer.decode(["va", "?"]), "va?")

    def test_nllb_decode_also_strips_space_before_question_mark(self) -> None:
        tokenizer = ct2tok.CT2Tokenizer(ct2tok.NLLB, self.sp_path, self.vocab_path)
        with patch.object(tokenizer._sp, "decode_pieces", return_value="va ?"):
            self.assertEqual(tokenizer.decode(["va", "?"]), "va?")


class TokenizerFilesTests(unittest.TestCase):
    def _makeSnapshot(self, cache_dir: str, repo_id: str, filenames) -> str:
        snapshot = os.path.join(cache_dir, "models--" + repo_id.replace("/", "--"), "snapshots", "abc123")
        os.makedirs(snapshot)
        for name in filenames:
            with open(os.path.join(snapshot, name), "wb") as f:
                f.write(b"x")
        return snapshot

    def test_finds_files_in_existing_hf_cache_layout(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            snapshot = self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model", "vocab.json"])
            files = ct2tok.findTokenizerFiles(cache_dir, "facebook/m2m100_418M", ct2tok.REQUIRED_FILES[ct2tok.M2M100])
        self.assertEqual(files["vocab.json"], os.path.join(snapshot, "vocab.json"))

    def test_returns_none_when_a_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model"])
            files = ct2tok.findTokenizerFiles(cache_dir, "facebook/m2m100_418M", ct2tok.REQUIRED_FILES[ct2tok.M2M100])
        self.assertIsNone(files)

    def test_does_not_download_when_cache_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            self._makeSnapshot(cache_dir, "facebook/m2m100_418M", ["sentencepiece.bpe.model", "vocab.json"])
            with patch.object(ct2tok, "downloadTokenizerFiles") as mock_download, \
                 patch.object(ct2tok, "CT2Tokenizer") as mock_tokenizer:
                ct2tok.loadCT2Tokenizer(cache_dir, "facebook/m2m100_418M", "m2m100_418M-ct2-int8")
        mock_download.assert_not_called()
        self.assertEqual(mock_tokenizer.call_args.args[0], ct2tok.M2M100)

    def test_downloads_when_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            fake_files = {"sentencepiece.bpe.model": "a", "tokenizer.json": "b"}
            with patch.object(ct2tok, "downloadTokenizerFiles", return_value=fake_files) as mock_download, \
                 patch.object(ct2tok, "CT2Tokenizer") as mock_tokenizer:
                ct2tok.loadCT2Tokenizer(cache_dir, "facebook/nllb-200-distilled-600M", "nllb-200-distilled-600M-ct2-int8")
        mock_download.assert_called_once_with(
            cache_dir, "facebook/nllb-200-distilled-600M", ct2tok.REQUIRED_FILES[ct2tok.NLLB])
        mock_tokenizer.assert_called_once_with(ct2tok.NLLB, "a", "b")


_SENTENCES = {
    "ja": ["こんにちは、元気ですか？", "今日はいい天気ですね。", "VRChat で会いましょう。", "この翻訳は正しいですか？", "東京タワーに行きたい。"],
    "en": ["Hello, how are you?", "The weather is nice today.", "Let's meet in VRChat.", "Is this translation correct?", "I want to visit Tokyo Tower."],
    "ko": ["안녕하세요, 잘 지내세요?", "오늘 날씨가 좋네요.", "VRChat에서 만나요.", "이 번역이 맞나요?", "도쿄 타워에 가고 싶어요."],
    "zh": ["你好，你好吗？", "今天天气很好。", "我们在 VRChat 见面吧。", "这个翻译正确吗？", "我想去东京塔。"],
    "fr": ["Bonjour, comment ça va ?", "Il fait beau aujourd'hui.", "Rendez-vous sur VRChat.", "Cette traduction est-elle correcte ?", "Je veux visiter la tour de Tokyo."],
    "de": ["Hallo, wie geht es dir?", "Das Wetter ist heute schön.", "Treffen wir uns in VRChat.", "Ist diese Übersetzung richtig?", "Ich möchte den Tokyo Tower besuchen."],
}
_NLLB_CODES = {"ja": "jpn_Jpan", "en": "eng_Latn", "ko": "kor_Hang", "zh": "zho_Hans", "fr": "fra_Latn", "de": "deu_Latn"}
_PARITY_MODELS = [
    ("m2m100_418M-ct2-int8", "facebook/m2m100_418M", lambda lang: lang),
    ("nllb-200-distilled-600M-ct2-int8", "facebook/nllb-200-distilled-600M", lambda lang: _NLLB_CODES[lang]),
]


class TransformersParityTests(unittest.TestCase):
    """transformers 版と自前版で、トークン列とデコード結果が一致すること。

    transformers は requirements-dev.txt にだけ残す。トークナイザファイルが
    src-python/weights に無い環境ではスキップする (ネットワークに出ない)。
    """

    def test_tokens_and_decoding_match_transformers(self) -> None:
        try:
            import transformers
        except ImportError:
            self.skipTest("transformers is not installed")
        from config import config
        for weight_type, repo_id, to_code in _PARITY_MODELS:
            cache_dir = os.path.join(config.PATH_LOCAL, "weights", "ctranslate2", weight_type, "tokenizer")
            family = ct2tok.tokenizerFamily(weight_type)
            if ct2tok.findTokenizerFiles(cache_dir, repo_id, ct2tok.REQUIRED_FILES[family]) is None:
                self.skipTest(f"tokenizer files for {weight_type} are not cached")
            ours = ct2tok.loadCT2Tokenizer(cache_dir, repo_id, weight_type)
            theirs = transformers.AutoTokenizer.from_pretrained(repo_id, cache_dir=cache_dir, local_files_only=True)
            for lang, sentences in _SENTENCES.items():
                code = to_code(lang)
                for sentence in sentences:
                    with self.subTest(model=weight_type, lang=lang, sentence=sentence):
                        theirs.src_lang = code
                        expected = theirs.convert_ids_to_tokens(theirs.encode(sentence))
                        actual = ours.encode(sentence, code)
                        self.assertEqual(actual, expected)
                        body = actual[1:-1]
                        self.assertEqual(ours.decode(body), theirs.decode(theirs.convert_tokens_to_ids(body)))


if __name__ == "__main__":
    unittest.main()
