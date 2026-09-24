"""Whisper 入力用の帯域制限付きリサンプリング。

`AudioData.get_raw_data(convert_rate=16000)` は内部で `audioop.ratecv`
(線形補間、ローパス無し) を使う。48kHz のマイクから 16kHz へ落とすと
8kHz 以上の成分がそのまま折り返して (エイリアシング) 子音帯域に雑音として
乗り、Whisper の認識精度が落ちる。ここでは FFT で 8kHz 以上を切り捨てて
から長さを変える (理想ローパス + リサンプル) ことで折り返しを無くす。
"""

import numpy as np

WHISPER_SAMPLE_RATE = 16000

# FFT は信号を周期的とみなすため、端と端が不連続だと先頭/末尾に僅かな
# にじみが出る。反射パディングで端を滑らかにしてから切り戻す。
_EDGE_PAD_SECONDS = 0.05


def resample_float32(samples: np.ndarray, sample_rate: int, target_rate: int = WHISPER_SAMPLE_RATE) -> np.ndarray:
    """float32 モノラル波形を target_rate へ帯域制限付きでリサンプルする。"""
    samples = np.asarray(samples, dtype=np.float32)
    if sample_rate == target_rate or samples.size == 0:
        return samples
    expected = int(round(samples.size * target_rate / sample_rate))
    if expected == 0:
        return np.zeros(0, dtype=np.float32)

    pad = min(int(sample_rate * _EDGE_PAD_SECONDS), samples.size - 1)
    padded = np.pad(samples, (pad, pad), mode="reflect") if pad > 0 else samples

    out_len = int(round(padded.size * target_rate / sample_rate))
    spectrum = np.fft.rfft(padded)
    bins = out_len // 2 + 1
    if bins <= spectrum.size:
        spectrum = spectrum[:bins]
    else:
        spectrum = np.concatenate([spectrum, np.zeros(bins - spectrum.size, dtype=spectrum.dtype)])
    resampled = np.fft.irfft(spectrum, n=out_len) * (out_len / padded.size)

    out_pad = int(round(pad * target_rate / sample_rate))
    return resampled[out_pad:out_pad + expected].astype(np.float32)


def resample_pcm16_to_float32(data: bytes, sample_rate: int, target_rate: int = WHISPER_SAMPLE_RATE) -> np.ndarray:
    """16bit モノラル PCM を Whisper が受け取る [-1, 1] の float32 16kHz 波形にする。"""
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return np.clip(resample_float32(samples, sample_rate, target_rate), -1.0, 1.0)


class StreamingResampler:
    """音声を少しずつ受け取りながら帯域制限付きでリサンプルする。

    VAD の経路 (audio_vad.Pcm16MonoNormalizer) は録音の小さな塊ごとに
    16kHz へ落とすため、クリップ全体を一度に扱う resample_float32 は使えない。
    ここでは Kaiser 窓付き sinc のローパスで各出力サンプルを直接計算し、
    塊の境目をまたぐ分の入力を持ち越す。出力は各塊の末尾 _ZERO_CROSSINGS
    周期分 (48kHz→16kHz で約 1ms) だけ遅れて出る。
    """

    _ZERO_CROSSINGS = 16
    _KAISER_BETA = 8.6

    def __init__(self, in_rate: int, out_rate: int = WHISPER_SAMPLE_RATE) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        # 1出力サンプルあたりに進む入力サンプル数と、入力の Nyquist に対する
        # 遮断周波数の比 (下げるときは出力の Nyquist で切る)。
        self._step = in_rate / out_rate
        self._cutoff = min(1.0, out_rate / in_rate)
        self._half = int(np.ceil(self._ZERO_CROSSINGS / self._cutoff))
        self._offsets = np.arange(-self._half + 1, self._half + 1)
        self.reset()

    def reset(self) -> None:
        # 先頭の前は無音とみなし、最初の出力は入力の先頭サンプルの位置に置く。
        self._buffer = np.zeros(self._half, dtype=np.float64)
        self._position = float(self._half)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """int16 か float のモノラル波形を受け取り、出せる分を float64 で返す。"""
        if self.in_rate == self.out_rate:
            return np.asarray(samples, dtype=np.float64)
        self._buffer = np.concatenate([self._buffer, np.asarray(samples, dtype=np.float64)])
        last = self._buffer.size - self._half  # この位置未満なら右側の窓が揃う
        count = max(0, int(np.ceil((last - self._position) / self._step)))
        if count == 0:
            return np.zeros(0, dtype=np.float64)

        positions = self._position + np.arange(count) * self._step
        base = np.floor(positions).astype(np.int64)
        indices = base[:, None] + self._offsets[None, :]
        distance = indices - positions[:, None]
        ratio = np.clip(distance / self._half, -1.0, 1.0)
        window = np.i0(self._KAISER_BETA * np.sqrt(1.0 - ratio * ratio)) / np.i0(self._KAISER_BETA)
        weights = self._cutoff * np.sinc(self._cutoff * distance) * window
        output = np.einsum("ij,ij->i", self._buffer[indices], weights)

        # 次の出力に要る分 (次の位置の左側の窓) だけを残す。
        next_position = self._position + count * self._step
        keep_from = int(np.floor(next_position)) - self._half + 1
        self._buffer = self._buffer[keep_from:]
        self._position = next_position - keep_from
        return output
