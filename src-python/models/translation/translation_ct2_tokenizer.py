"""CTranslate2 翻訳モデル (M2M100 / NLLB) 用のトークナイザ。

以前は transformers.AutoTokenizer を使っていたが、transformers は import が重く
(起動時間)、使っていたのはトークン化だけだったので sentencepiece で置き換えた
(A-1, 2026-09-23)。出力は transformers の
`convert_ids_to_tokens(encode(text))` と同じトークン文字列の列で、
test_translation_ct2_tokenizer.py の照合テストで一致を確かめている。

トークナイザファイルは transformers が作った HF キャッシュ
(<cache_dir>/models--<org>--<name>/snapshots/<rev>/) をそのまま使い、
無ければ huggingface_hub で必要なファイルだけ同じ場所へ取得する。
"""

import json
import os
from os import path as os_path

M2M100 = "m2m100"
NLLB = "nllb"

# vocab 側のファイルは「sentencepiece のピースのうち、モデルの語彙にあるもの」を
# 知るために使う。語彙に無いピースは transformers と同じく <unk> にする。
REQUIRED_FILES = {
    M2M100: ("sentencepiece.bpe.model", "vocab.json"),
    NLLB: ("sentencepiece.bpe.model", "tokenizer.json"),
}
_VOCAB_FILE = {M2M100: "vocab.json", NLLB: "tokenizer.json"}
_SPECIAL_TOKENS = frozenset({"<s>", "</s>", "<pad>"})


def _cleanUpTokenizationSpaces(text: str) -> str:
    # transformers.PreTrainedTokenizerBase.clean_up_tokenization: both m2m100
    # and NLLB omit clean_up_tokenization_spaces in tokenizer_config.json, so
    # it resolves to the library default (True) for both, and both need this
    # to match (verified: without it, decode differs from transformers' on
    # French " ?"/" !" spacing for both families).
    return (
        text.replace(" .", ".")
        .replace(" ?", "?")
        .replace(" !", "!")
        .replace(" ,", ",")
        .replace(" ' ", "'")
        .replace(" n't", "n't")
        .replace(" 'm", "'m")
        .replace(" 's", "'s")
        .replace(" 've", "'ve")
        .replace(" 're", "'re")
    )


def tokenizerFamily(weight_type: str) -> str:
    if weight_type.startswith("m2m100"):
        return M2M100
    if weight_type.startswith("nllb"):
        return NLLB
    raise ValueError(f"unknown CTranslate2 weight type: {weight_type}")


def _loadVocab(vocab_path: str) -> frozenset:
    with open(vocab_path, encoding="utf-8") as f:
        data = json.load(f)
    vocab = data["model"]["vocab"] if isinstance(data.get("model"), dict) else data
    if isinstance(vocab, dict):
        return frozenset(vocab)
    return frozenset(entry[0] for entry in vocab)


class CT2Tokenizer:
    def __init__(self, family: str, sp_model_path: str, vocab_path: str) -> None:
        import sentencepiece
        self.family = family
        self._sp = sentencepiece.SentencePieceProcessor(model_file=sp_model_path)
        self._vocab = _loadVocab(vocab_path)

    def _langToken(self, lang: str) -> str:
        return f"__{lang}__" if self.family == M2M100 else lang

    def encode(self, text: str, source_lang: str) -> list[str]:
        pieces = [p if p in self._vocab else "<unk>" for p in self._sp.encode(text, out_type=str)]
        return [self._langToken(source_lang)] + pieces + ["</s>"]

    def targetPrefix(self, target_lang: str) -> list[str]:
        return [self._langToken(target_lang)]

    def decode(self, tokens: list[str]) -> str:
        text = self._sp.decode_pieces([t for t in tokens if t not in _SPECIAL_TOKENS])
        return _cleanUpTokenizationSpaces(text)


def findTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple) -> dict | None:
    snapshots = os_path.join(cache_dir, "models--" + repo_id.replace("/", "--"), "snapshots")
    if not os_path.isdir(snapshots):
        return None
    for revision in sorted(os.listdir(snapshots)):
        directory = os_path.join(snapshots, revision)
        if all(os_path.isfile(os_path.join(directory, name)) for name in filenames):
            return {name: os_path.join(directory, name) for name in filenames}
    return None


def downloadTokenizerFiles(cache_dir: str, repo_id: str, filenames: tuple) -> dict:
    from huggingface_hub import hf_hub_download
    return {name: hf_hub_download(repo_id=repo_id, filename=name, cache_dir=cache_dir) for name in filenames}


def loadCT2Tokenizer(cache_dir: str, repo_id: str, weight_type: str) -> CT2Tokenizer:
    family = tokenizerFamily(weight_type)
    filenames = REQUIRED_FILES[family]
    files = findTokenizerFiles(cache_dir, repo_id, filenames) or downloadTokenizerFiles(cache_dir, repo_id, filenames)
    return CT2Tokenizer(family, files["sentencepiece.bpe.model"], files[_VOCAB_FILE[family]])
