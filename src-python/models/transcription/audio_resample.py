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
