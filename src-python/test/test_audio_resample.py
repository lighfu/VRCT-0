"""models/transcription/audio_resample.py のテスト。"""

import audioop
import unittest

import numpy as np

from models.transcription.audio_resample import StreamingResampler, resample_float32, resample_pcm16_to_float32


def _tone(freq: float, sample_rate: int, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


class TestResample(unittest.TestCase):
    def test_same_rate_is_returned_as_is(self) -> None:
        x = _tone(440, 16000)
        np.testing.assert_array_equal(resample_float32(x, 16000), x)

    def test_output_length_matches_the_rate_ratio(self) -> None:
        for rate in (48000, 44100, 32000, 22050, 8000):
            x = _tone(440, rate, seconds=1.3)
            self.assertEqual(resample_float32(x, rate).size, int(round(x.size * 16000 / rate)), rate)

    def test_speech_band_tone_is_preserved(self) -> None:
        y = resample_float32(_tone(1000, 48000), 48000)
        expected = _tone(1000, 16000)
        # 端の反射パディング付近を除いた区間で元の正弦波と一致する。
        np.testing.assert_allclose(y[800:-800], expected[800:-800], atol=1e-3)

    def test_content_above_8khz_is_removed_instead_of_aliased(self) -> None:
        """audioop.ratecv では 12kHz が 4kHz に折り返して残る。"""
        tone = _tone(12000, 48000)
        pcm = (tone * 32767).astype("<i2").tobytes()

        ours = resample_pcm16_to_float32(pcm, 48000)
        ratecv, _ = audioop.ratecv(pcm, 2, 1, 48000, 16000, None)
        theirs = np.frombuffer(ratecv, dtype=np.int16).astype(np.float32) / 32768.0

        self.assertGreater(_rms(theirs), 0.1)
        self.assertLess(_rms(ours[800:-800]), 0.01)

    def test_empty_input(self) -> None:
        self.assertEqual(resample_pcm16_to_float32(b"", 48000).size, 0)

    def test_very_short_input_does_not_crash(self) -> None:
        self.assertEqual(resample_pcm16_to_float32(b"\x01\x00", 48000).dtype, np.float32)


class TestStreamingResampler(unittest.TestCase):
    def _run(self, x: np.ndarray, rate: int, chunk: int) -> np.ndarray:
        resampler = StreamingResampler(rate)
        return np.concatenate([resampler.process(x[i:i + chunk]) for i in range(0, x.size, chunk)])

    def test_output_does_not_depend_on_chunk_size(self) -> None:
        x = _tone(700, 44100, seconds=0.5) * 20000
        small = self._run(x, 44100, 441)
        large = self._run(x, 44100, 1000)
        n = min(small.size, large.size)
        np.testing.assert_allclose(small[:n], large[:n], atol=1e-6)

    def test_speech_band_tone_matches_the_one_shot_resampler(self) -> None:
        x = _tone(1000, 48000)
        streamed = self._run(x, 48000, 960)
        expected = resample_float32(x, 48000)[:streamed.size]
        np.testing.assert_allclose(streamed[800:], expected[800:], atol=2e-3)

    def test_content_above_8khz_is_removed(self) -> None:
        streamed = self._run(_tone(12000, 48000), 48000, 960)
        self.assertLess(_rms(streamed[100:]), 0.005)

    def test_reset_forgets_the_previous_stream(self) -> None:
        resampler = StreamingResampler(48000)
        resampler.process(np.full(4800, 20000.0))
        resampler.reset()
        self.assertLess(np.abs(resampler.process(np.zeros(4800))).max(), 1e-9)


if __name__ == "__main__":
    unittest.main()
