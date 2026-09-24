"""文字起こしエンジンをプラガブルにするためのプロバイダ抽象化。

`transcription_transcriber.py` の `transcribeAudioQueue()` が
`match self.transcription_engine: case "Google": ... case "Whisper": ...`
という形でエンジンごとの処理をハードコードしていたのを、プロバイダ
登録ベースのディスパッチに置き換える (バックエンドレビュー Phase 3
項目17 の `TranslationProvider` レジストリと同じ考え方を文字起こし側にも
展開したもの)。

VRCT の既存パイプラインは「フレーズ確定後にまとめて1回で送信する」
バッチ型のため、プロバイダのインターフェースも同期的な `transcribe()`
のみとする (常時ストリーミング前提の非同期セッション型は採用しない)。

言語候補 (`languages`/`countries`) をまたいだ「最も確信度の高い結果を
選ぶ」ループは呼び出し元 (`transcribeAudioQueue`) に共通化し、各
プロバイダは「1つの言語候補に対して1回試行する」ことだけを担当する。
"""

import math
import numbers
import re
from typing import List, Optional, Protocol, Tuple

import numpy as np
import requests
from speech_recognition import AudioData, Recognizer, UnknownValueError

from errors import ErrorCode
from models.transcription.audio_resample import WHISPER_SAMPLE_RATE, resample_pcm16_to_float32
from models.transcription.transcription_deepgram import resolveDeepgramLanguageCode
from models.transcription.transcription_languages import transcription_lang
from utils import errorLogging

# setup.exe ダウンロード等の HTTP 呼び出しで使っている (10, 60) と同じ
# (connect, read) タイムアウト。実機検証の結果次第で調整する。
_HTTP_TIMEOUT = (10, 60)


class TranscriptionApiError(Exception):
    """API 系エンジンでの文字起こし失敗を、対応する `ErrorCode` 付きで表す。"""

    def __init__(self, error_code: ErrorCode, message: str = "") -> None:
        super().__init__(message or error_code.value)
        self.error_code = error_code


class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        """1つの言語候補に対して1回だけ試行する。

        Returns:
            (text, confidence, is_definitive) のタプル。
            - text: 認識結果 (認識できなければ "")
            - confidence: 信頼度 (呼び出し元が複数候補から最良を選ぶ)
            - is_definitive: True の場合、検出された言語が要求した言語と
              一致した等の理由でこれ以上他の候補を試す必要が無いことを
              示す (呼び出し元はこの合図でループを早期終了できる)。
        """
        ...

    # 任意: 自分で言語を判定できるプロバイダは `chooses_language = True` とし、
    #
    #   choose_language(audio_data, languages, countries, *, no_speech_prob) -> Optional[int]
    #
    # を持つ。候補が複数のとき、呼び出し元はまずこれを1回呼び、返った添字の
    # 候補だけを force_language=True で文字起こしする (候補ごとに推論を
    # 繰り返さない)。None なら従来通り候補を順に試す。


class GoogleProvider:
    """既存の `recognize_google` 呼び出しをそのまま包む。

    Google は候補言語ごとに別プロトコル呼び出しが必要で「検出された
    言語」という概念が無いため、`is_definitive` は常に False (呼び出し元は
    全候補を試行する、既存の挙動と同じ)。
    """

    def __init__(self, recognizer: Recognizer) -> None:
        self._recognizer = recognizer

    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        # 2026-09-07: クリップ前後の無音パディングやクリップ分割を
        # このプロバイダ内で行う対策を試したが、実機検証の結果「クリップの
        # 前後に無音パディングを付与する」対策はエンジンを問わず
        # AudioTranscriber 側 (transcription_transcriber.py) で一律に適用する
        # 形に一本化した。Google はこれに加えて「育っていくバッファを都度
        # 再送信する」(interim_send、AudioTranscriber側) も併用しているが、
        # このプロバイダ自体は毎回渡された audio_data を1回認識するだけで
        # 良く、パディングや再送信ロジックを知る必要はない。
        try:
            # join_all_results=True: このエンドポイントは、1クリップに
            # 複数の発話区間 (無音を挟んだ複数の文) が含まれる場合、それ
            # ぞれを別々の result ブロックとして返すことがある。既定
            # (最初のブロックだけを使う) のままだと後続の発話が黙って
            # 失われる (2026-09-07、実機で確認・custom_speech_recognition
            # フォーク側で修正)。これは上記のパディング対策とは独立した
            # 別の不具合修正なので撤回せず維持する。
            text, confidence = self._recognizer.recognize_google(
                audio_data,
                language=transcription_lang[language][country]["Google"],
                with_confidence=True,
                join_all_results=True,
            )
        except UnknownValueError:
            return "", 0.0, False
        return text, confidence, False


class _PreparedAudio:
    """AudioData を Whisper/SenseVoice が受け取る 16kHz float32 に直し、同じ
    クリップを候補言語ごとに呼び直すときは直した結果を使い回す。

    get_raw_data(convert_rate=16000) は audioop.ratecv (ローパス無しの線形補間)
    で 48kHz → 16kHz を行い、8kHz 以上が折り返して認識精度を落とす。元の
    レートのまま取り出し、帯域制限付きでリサンプルする。
    """

    def __init__(self) -> None:
        self._source: Optional[AudioData] = None
        self._audio: Optional[np.ndarray] = None

    def get(self, audio_data: AudioData) -> np.ndarray:
        if self._source is audio_data and self._audio is not None:
            return self._audio
        sample_rate = getattr(audio_data, "sample_rate", WHISPER_SAMPLE_RATE)
        if not isinstance(sample_rate, numbers.Integral) or sample_rate <= 0:
            sample_rate = WHISPER_SAMPLE_RATE
        raw = audio_data.get_raw_data(convert_width=2)
        self._audio = resample_pcm16_to_float32(raw, int(sample_rate))
        self._source = audio_data
        return self._audio


def _languageCode(language: str, country: str, engine: str) -> Optional[str]:
    return transcription_lang.get(language, {}).get(country, {}).get(engine)


class LocalWhisperProvider:
    """既存のローカル (faster-whisper/CTranslate2) 呼び出しをそのまま包む。"""

    chooses_language = True

    def __init__(self, whisper_model) -> None:
        self._whisper_model = whisper_model
        self._audio = _PreparedAudio()

    def choose_language(
        self,
        audio_data: AudioData,
        languages: List[str],
        countries: List[str],
        *,
        no_speech_prob: float = 0.6,
    ) -> Optional[int]:
        """候補言語が複数あるとき、言語判定を1回だけ行い最も確からしい候補の
        添字を返す。

        以前は候補ごとに `language=None` で丸ごと文字起こしを繰り返していた。
        言語を指定しない呼び出しは毎回同じ結果になるため、判定結果が候補と
        一致しないと候補の数だけ同じ推論が走り、その上で候補外の言語
        (日本語の発話を中国語と判定する等) の結果が採用されていた。
        判定を候補の中に絞り、その言語を指定して1回だけ文字起こしする。
        判定できない場合は None を返し、呼び出し元は従来通り候補を順に試す。
        """
        try:
            _, _, all_probs = self._whisper_model.detect_language(self._audio.get(audio_data))
            probs = {code: float(prob) for code, prob in all_probs}
        except Exception:
            errorLogging()
            return None

        best_index = None
        best_prob = -1.0
        for index, (language, country) in enumerate(zip(languages, countries)):
            code = _languageCode(language, country, "Whisper")
            if code is None:
                continue
            prob = probs.get(code, 0.0)
            if prob > best_prob:
                best_index, best_prob = index, prob
        return best_index

    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        raw = self._audio.get(audio_data)

        source_language = transcription_lang[language][country]["Whisper"] if force_language else None
        segments, info = self._whisper_model.transcribe(
            raw,
            beam_size=5,
            temperature=0.0,
            log_prob_threshold=avg_logprob,
            no_speech_threshold=no_speech_prob,
            language=source_language,
            word_timestamps=False,
            without_timestamps=True,
            task="transcribe",
            no_repeat_ngram_size=no_repeat_ngram_size,
        )
        text = ""
        for s in segments:
            if s.avg_logprob < avg_logprob or s.no_speech_prob > no_speech_prob:
                continue
            text += s.text

        is_definitive = force_language or transcription_lang[language][country]["Whisper"] == info.language
        return text, info.language_probability, is_definitive


# SenseVoice は日本語・中国語でも単語や数字・英単語の前後に空白を入れて返す
# ことがある (例: "今日 は 3 時 に VRChat で 遊ぶ")。かな・漢字・全角記号の
# 隣にある空白を詰め、英単語どうしの間の空白は残す。
_CJK = "\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef"
_SPACE_NEXT_TO_CJK = re.compile(rf"(?<=[{_CJK}])\s+(?=\S)|(?<=\S)\s+(?=[{_CJK}])")
# 空白を詰める言語 (韓国語は語の間に空白を入れる言語なので含めない)。
_NO_SPACE_LANGUAGES = ("ja", "zh", "yue")


class SenseVoiceProvider:
    """ローカル SenseVoice (sherpa-onnx) 向けプロバイダ。

    - 言語を自動判定する認識器 (マイクとスピーカーで共有) で1回だけ推論し、
      候補言語ごとの呼び出しではその結果を使い回す。
    - 判定された言語が候補に無ければ、候補が1言語 (force_language) でも採用
      しない。sherpa-onnx は発話ごとに言語を変えられないため、読み直すには
      言語を固定した認識器 (約 240MB) を別に読み込む必要があり、軽さを優先して
      捨てる。Common Voice 日本語 60文では自動判定は全文で ja と当たり、
      日本語に固定したときとの CER の差も 0.3 ポイントだった。
    - SenseVoice は無音や雑音にも "그." のような短い文と普通の言語タグを返し、
      信頼度も返さない。そこで推論の前に Silero VAD で発話があるかを確かめ、
      無ければ推論しない。VAD のしきい値は no_speech_prob の設定から決める
      (1 - no_speech_prob。既定の 0.6 なら 0.4)。なお BGM などのイベントの
      タグは使わない (BGM に重ねた発話の2割が BGM と判定されたため)。
    - 対応していない言語 (フランス語等) の候補は何も返さない。
    """

    chooses_language = True

    def __init__(self, recognizer) -> None:
        self._recognizer = recognizer
        self._audio = _PreparedAudio()
        self._decoded_for: Optional[AudioData] = None
        self._decoded: Tuple[str, str] = ("", "")

    def _hasSpeech(self, audio: np.ndarray, no_speech_prob: float) -> bool:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        threshold = min(max(1.0 - no_speech_prob, 0.1), 0.9)
        return len(get_speech_timestamps(audio, VadOptions(threshold=threshold))) > 0

    @staticmethod
    def _cleanText(text: str, detected: str) -> str:
        if detected in _NO_SPACE_LANGUAGES:
            text = _SPACE_NEXT_TO_CJK.sub("", text)
        return text.strip()

    def _decode(self, audio_data: AudioData, no_speech_prob: float) -> Tuple[str, str]:
        """自動判定の認識器で推論し (text, 判定された言語) を返す。発話が
        無ければ ("", "")。"""
        if self._decoded_for is audio_data:
            return self._decoded
        audio = self._audio.get(audio_data)
        text, detected = "", ""
        if audio.size > 0 and self._hasSpeech(audio, no_speech_prob):
            text, detected = self._recognizer.decode(audio, WHISPER_SAMPLE_RATE)
            if detected == "nospeech":
                text, detected = "", ""
            text = self._cleanText(text, detected)
        self._decoded_for = audio_data
        self._decoded = (text, detected)
        return self._decoded

    def choose_language(
        self,
        audio_data: AudioData,
        languages: List[str],
        countries: List[str],
        *,
        no_speech_prob: float = 0.6,
    ) -> Optional[int]:
        """判定された言語に一致する候補の添字。一致しなければ None。"""
        _, detected = self._decode(audio_data, no_speech_prob)
        for index, (language, country) in enumerate(zip(languages, countries)):
            if detected and _languageCode(language, country, "SenseVoice") == detected:
                return index
        return None

    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        code = _languageCode(language, country, "SenseVoice")
        if code is None:
            return "", 0.0, False
        text, detected = self._decode(audio_data, no_speech_prob)
        if not text:
            return "", 0.0, False
        if detected != code:
            return "", 0.0, False
        return text, 1.0, True


OpenAI = None  # 使うときに _openai() が読み込む (起動時間の短縮, A-1)


def _openai():
    global OpenAI
    if OpenAI is None:
        from openai import OpenAI as _cls
        OpenAI = _cls
    return OpenAI


def _map_openai_exception(exc: Exception) -> ErrorCode:
    # isinstance 判定だけなので、openai パッケージ自体は使うときにだけ読み込む。
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        RateLimitError,
    )
    if isinstance(exc, AuthenticationError):
        return ErrorCode.TRANSCRIPTION_API_AUTH_FAILED
    if isinstance(exc, RateLimitError):
        return ErrorCode.TRANSCRIPTION_API_RATE_LIMITED
    if isinstance(exc, APITimeoutError):
        return ErrorCode.TRANSCRIPTION_API_TIMEOUT
    if isinstance(exc, (APIStatusError, APIConnectionError)):
        return ErrorCode.TRANSCRIPTION_API_SERVER_ERROR
    return ErrorCode.TRANSCRIPTION_API_SERVER_ERROR


class OpenAICompatibleTranscriptionProvider:
    """OpenAI互換の音声書き起こしREST API (`/v1/audio/transcriptions`) 向け
    プロバイダ。公式 `openai` パッケージ (既存依存) の
    `client.audio.transcriptions.create(...)` を使う。

    Groq/OpenAI公式/issue #100 のカスタムローカルサーバーを、
    base_url/api_key/model の差し替えだけで1つの実装でカバーする
    (ユーザーから見えるエンジン選択肢としては、翻訳側の
    `OpenAI_API`/`Groq_API`/`OpenAI_Compatible` が別々の選択肢であるのと
    同じ構造で、`Groq_Whisper`/`OpenAI_Whisper`/`Custom_Whisper` として
    それぞれ専用のインスタンスを持つ)。

    音声フォーマットは各API先の仕様に準拠する方針のため、現時点では
    最も広くサポートされている WAV (`AudioData.get_wav_data()`) を使う。
    """

    def __init__(self, api_key: str, base_url: str, model: str, engine_name: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.engine_name = engine_name
        self._client = _openai()(api_key=api_key, base_url=base_url, timeout=_HTTP_TIMEOUT)

    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        wav_bytes = audio_data.get_wav_data(convert_rate=16000, convert_width=2)
        source_language = transcription_lang[language][country][self.engine_name] if force_language else None

        try:
            response = self._client.audio.transcriptions.create(
                file=("audio.wav", wav_bytes, "audio/wav"),
                model=self.model,
                language=source_language,
                response_format="verbose_json",
                temperature=0.0,
            )
        except Exception as exc:
            error_code = _map_openai_exception(exc)
            errorLogging()
            raise TranscriptionApiError(error_code) from exc

        segments = getattr(response, "segments", None) or []
        accepted_logprobs: List[float] = []
        text = ""
        if segments:
            for s in segments:
                if s.avg_logprob < avg_logprob or s.no_speech_prob > no_speech_prob:
                    continue
                text += s.text
                accepted_logprobs.append(s.avg_logprob)
        else:
            # verbose_json 非対応のサーバー (カスタムサーバー等) は
            # segments を返さないことがある。その場合はセグメント単位の
            # フィルタリングができないため、テキストをそのまま採用する。
            text = getattr(response, "text", "") or ""

        if not text:
            return "", 0.0, False

        # avg_logprob (対数確率、概ね負の値) を 0〜1 の疑似的な信頼度に変換する。
        # ローカル Whisper の info.language_probability に相当するものが
        # verbose_json には無いため、代替の指標として使う。
        confidence = math.exp(sum(accepted_logprobs) / len(accepted_logprobs)) if accepted_logprobs else 0.5

        detected_language = getattr(response, "language", None)
        is_definitive = force_language or (
            detected_language is not None
            and detected_language == transcription_lang[language][country][self.engine_name]
        )
        return text, confidence, is_definitive


_DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"


def _map_deepgram_status(status_code: int) -> ErrorCode:
    if status_code in (401, 403):
        return ErrorCode.TRANSCRIPTION_API_AUTH_FAILED
    if status_code == 429:
        return ErrorCode.TRANSCRIPTION_API_RATE_LIMITED
    return ErrorCode.TRANSCRIPTION_API_SERVER_ERROR


class DeepgramProvider:
    """Deepgram の録音済み(バッチ) REST API (`/v1/listen`) 向けプロバイダ。

    OpenAI互換ではないため `openai` パッケージは使わず、既存依存の
    `requests` で直接叩く。

    言語コードについて: 候補言語が1つに確定している場合
    (`force_language=True`)、`resolveDeepgramLanguageCode()` で
    このモデルが実際に対応言語として申告しているコード (Google列との
    完全一致、または Whisper列のベースコード一致) を解決できれば、
    それを `language=` として明示的に渡す。解決できない場合
    (対応コードが不明、または候補言語が複数で1つに絞れない場合) は
    `detect_language=true` (自動検出) にフォールバックする。
    いずれの経路でも1回のAPI呼び出しで完結するため
    (Deepgram自身が1呼び出しで多言語を判定できる、Whisper系のように
    候補言語ごとに複数回呼ぶ必要が無い)、`is_definitive` は常に True を
    返し、呼び出し元のループを1回で打ち切らせる。

    信頼度についても、avg_logprob/no_speech_prob に相当するセグメント
    単位の指標をDeepgramは返さないため、トップレベルの `confidence`
    (0〜1) をそのまま使う (セグメント単位のフィルタリングは行わない)。
    """

    def __init__(self, api_key: str, model: str, model_languages: Optional[List[str]] = None) -> None:
        self.api_key = api_key
        self.model = model
        self.model_languages = model_languages or []

    def transcribe(
        self,
        audio_data: AudioData,
        language: str,
        country: str,
        *,
        avg_logprob: float,
        no_speech_prob: float,
        no_repeat_ngram_size: int,
        force_language: bool,
    ) -> Tuple[str, float, bool]:
        wav_bytes = audio_data.get_wav_data(convert_rate=16000, convert_width=2)

        params = {"model": self.model}
        resolved_code = resolveDeepgramLanguageCode(language, country, self.model_languages) if force_language else None
        if resolved_code:
            params["language"] = resolved_code
        else:
            params["detect_language"] = "true"

        try:
            response = requests.post(
                _DEEPGRAM_LISTEN_URL,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav",
                },
                params=params,
                data=wav_bytes,
                timeout=_HTTP_TIMEOUT,
            )
        except requests.exceptions.Timeout as exc:
            errorLogging()
            raise TranscriptionApiError(ErrorCode.TRANSCRIPTION_API_TIMEOUT) from exc
        except requests.exceptions.RequestException as exc:
            errorLogging()
            raise TranscriptionApiError(ErrorCode.TRANSCRIPTION_API_SERVER_ERROR) from exc

        if response.status_code != 200:
            errorLogging()
            raise TranscriptionApiError(_map_deepgram_status(response.status_code))

        payload = response.json()
        try:
            channel = payload["results"]["channels"][0]
            alternative = channel["alternatives"][0]
        except (KeyError, IndexError):
            return "", 0.0, False

        text = alternative.get("transcript", "") or ""
        if not text:
            return "", 0.0, False

        confidence = float(alternative.get("confidence", 0.0) or 0.0)
        # 明示コード・自動検出のいずれでも1回の呼び出しで完結するため、
        # この1回の結果が最終結果。呼び出し元 (transcribeAudioQueue) には
        # 他の候補言語を試させない。
        return text, confidence, True
