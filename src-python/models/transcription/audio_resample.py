"""Whisper 入力用の帯域制限付きリサンプリング。

`AudioData.get_raw_data(convert_rate=16000)` は内部で `audioop.ratecv`
(線形補間、ローパス無し) を使う。48kHz のマイクから 16kHz へ落とすと
8kHz 以上の成分がそのまま折り返して (エイリアシング) 子音帯域に雑音として
乗り、Whisper の認識精度が落ちる。ここでは FFT で 8kHz 以上を切り捨てて
から長さを変える (理想ローパス + リサンプル) ことで折り返しを無くす。
"""

import math

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
    ここでは Kaiser 窓付き sinc のローパスで各出力サンプルを計算し、塊の
    境目をまたぐ分の入力を持ち越す。出力は各塊の末尾 _ZERO_CROSSINGS 周期分
    (48kHz→16kHz で約 1ms) だけ遅れて出る。

    出力サンプルの位置の端数 (位相) は、入力と出力のレートの比で周期的に
    繰り返す (48k→16k は1通り、44.1k→16k は 160 通り)。位相ごとの重みを
    最初に1度だけ作り、process では表を引いて掛け合わせるだけにする
    (毎回 i0/sinc を計算すると、それだけで 1 コアの 1〜2 割を使っていた)。
    """

    _ZERO_CROSSINGS = 16
    _KAISER_BETA = 8.6

    def __init__(self, in_rate: int, out_rate: int = WHISPER_SAMPLE_RATE) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        divisor = math.gcd(in_rate, out_rate)
        # 1出力サンプルあたりに進む入力サンプル数を step_num / phases で表す。
        # 位置は「入力サンプル × phases」の整数で持つ (浮動小数の誤差をためない)。
        self._step_num = in_rate // divisor
        self._phases = out_rate // divisor
        # 入力の Nyquist に対する遮断周波数の比 (下げるときは出力の Nyquist で切る)。
        cutoff = min(1.0, out_rate / in_rate)
        self._half = int(np.ceil(self._ZERO_CROSSINGS / cutoff))
        self._offsets = np.arange(-self._half + 1, self._half + 1)
        fractions = np.arange(self._phases)[:, None] / self._phases
        distance = self._offsets[None, :] - fractions
        ratio = np.clip(distance / self._half, -1.0, 1.0)
        window = np.i0(self._KAISER_BETA * np.sqrt(1.0 - ratio * ratio)) / np.i0(self._KAISER_BETA)
        self._weights = cutoff * np.sinc(cutoff * distance) * window
        self.reset()

    def reset(self) -> None:
        # 先頭の前は無音とみなし、最初の出力は入力の先頭サンプルの位置に置く。
        self._buffer = np.zeros(self._half, dtype=np.float64)
        self._position = self._half * self._phases

    def process(self, samples: np.ndarray) -> np.ndarray:
        """int16 か float のモノラル波形を受け取り、出せる分を float64 で返す。"""
        if self.in_rate == self.out_rate:
            return np.asarray(samples, dtype=np.float64)
        self._buffer = np.concatenate([self._buffer, np.asarray(samples, dtype=np.float64)])
        # 位置の整数部 + half が buffer の末尾に収まる (右側の窓が揃う) 分だけ出す。
        limit = (self._buffer.size - self._half) * self._phases
        count = max(0, -(-(limit - self._position) // self._step_num))
        if count == 0:
            return np.zeros(0, dtype=np.float64)

        if self._phases == 1:
            # 整数比 (48k→16k 等): 位相は1通りなので、窓の並び (コピーしない
            # ビュー) を step_num おきに取り、重みとの積を1度で求める。
            windows = np.lib.stride_tricks.sliding_window_view(self._buffer, self._offsets.size)
            start = self._position - self._half + 1
            output = windows[start:start + count * self._step_num:self._step_num] @ self._weights[0]
        else:
            # 44.1k→16k 等: 出力ごとに窓と位相の重みを引く。
            positions = self._position + np.arange(count, dtype=np.int64) * self._step_num
            base, phase = np.divmod(positions, self._phases)
            indices = base[:, None] + self._offsets[None, :]
            output = np.einsum("ij,ij->i", self._buffer[indices], self._weights[phase])

        # 次の出力に要る分 (次の位置の左側の窓) だけを残す。
        next_position = self._position + count * self._step_num
        keep_from = next_position // self._phases - self._half + 1
        self._buffer = self._buffer[keep_from:]
        self._position = next_position - keep_from * self._phases
        return output
