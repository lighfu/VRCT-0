"""WASAPI の多チャンネルのマイクを、本来のチャンネル数で開いてモノラルにするテスト。

speech_recognition の Microphone はマイクを常に 1ch で開く。PyAudioWPatch の WASAPI
(共有モード) は、ミックス形式が 2ch 以上のデバイスを 1ch・ブロッキングで読むと
壊れたサンプルを返す (2026-09-25、Virtual Audio Cable に 440 Hz を流すと 240 Hz に化け、
1.8 秒で不連続点が約 265)。2ch で開けば正しく読めるので、WASAPI で 2ch 以上の
マイクだけ本来のチャンネル数で開き、読み出しで平均してモノラルにする。
"""

import struct
import unittest
from unittest.mock import MagicMock, patch

from speech_recognition import Microphone

from device_manager import _micDeviceWithHostApiType, paWASAPI
from models.transcription.transcription_recorder import (
    SelectedMicEnergyAndAudioRecorder,
    SelectedMicVadRecorder,
    _DownmixedMicrophone,
    _DownmixedStream,
    _downmixToMono,
)

_RECORDER = "models.transcription.transcription_recorder"
_PA_MME = 2  # PortAudio の paMME


def _pcm(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _opened_source(channels: int = 1):
    source = MagicMock()
    source.stream = object()
    source.SAMPLE_RATE = 48000
    source.SAMPLE_WIDTH = 2
    source.channels = channels
    return source


class DownmixToMonoTests(unittest.TestCase):
    def test_averages_interleaved_channels(self) -> None:
        self.assertEqual(
            _downmixToMono(_pcm(100, 300, -100, -300, 32767, 32767), 2),
            _pcm(200, -200, 32767),
        )

    def test_drops_an_incomplete_trailing_frame(self) -> None:
        self.assertEqual(_downmixToMono(_pcm(10, 20, 30), 2), _pcm(15))

    def test_handles_four_channels(self) -> None:
        self.assertEqual(_downmixToMono(_pcm(4, 8, 12, 16), 4), _pcm(10))

    def test_mono_passes_through_unchanged(self) -> None:
        data = _pcm(1, 2, 3)
        self.assertEqual(_downmixToMono(data, 1), data)

    def test_empty_input_gives_empty_output(self) -> None:
        self.assertEqual(_downmixToMono(b"", 2), b"")


class DownmixedStreamTests(unittest.TestCase):
    def test_read_returns_mono_and_keeps_the_pyaudio_stream(self) -> None:
        inner = MagicMock()
        inner.read.return_value = _pcm(100, 300)
        inner.pyaudio_stream = "pa-stream"
        stream = _DownmixedStream(inner, 2)

        self.assertEqual(stream.read(1), _pcm(200))
        inner.read.assert_called_once_with(1)
        # 停止処理 (_wrapStopperWithBlockingReadUnblock) は .pyaudio_stream を使う。
        self.assertEqual(stream.pyaudio_stream, "pa-stream")

        stream.close()
        inner.close.assert_called_once()


class DownmixedMicrophoneTests(unittest.TestCase):
    def test_opens_with_the_native_channel_count_and_reports_mono(self) -> None:
        fake_pyaudio = MagicMock()
        fake_pyaudio.paInt16 = 8
        fake_pyaudio.get_sample_size.return_value = 2
        fake_pyaudio.PyAudio.return_value.get_device_count.return_value = 8
        pa_stream = MagicMock()
        pa_stream.read.return_value = _pcm(100, 300)
        fake_pyaudio.PyAudio.return_value.open.return_value = pa_stream

        with patch.object(Microphone, "get_pyaudio", return_value=fake_pyaudio):
            mic = _DownmixedMicrophone(native_channels=2, device_index=5, sample_rate=48000)
            mic.__enter__()

        open_kwargs = fake_pyaudio.PyAudio.return_value.open.call_args.kwargs
        self.assertEqual(open_kwargs["channels"], 2)
        self.assertEqual(open_kwargs["input_device_index"], 5)
        # 後段 (エネルギー判定・VAD・文字起こし) からは 1ch のマイクに見える。
        self.assertEqual(mic.channels, 1)
        self.assertEqual(mic.stream.read(1), _pcm(200))
        self.assertIs(mic.stream.pyaudio_stream, pa_stream)

    def test_failed_open_leaves_the_stream_empty(self) -> None:
        fake_pyaudio = MagicMock()
        fake_pyaudio.paInt16 = 8
        fake_pyaudio.get_sample_size.return_value = 2
        fake_pyaudio.PyAudio.return_value.get_device_count.return_value = 8
        fake_pyaudio.PyAudio.return_value.open.side_effect = OSError("busy")

        with patch.object(Microphone, "get_pyaudio", return_value=fake_pyaudio):
            mic = _DownmixedMicrophone(native_channels=2, device_index=5, sample_rate=48000)
            mic.__enter__()

        self.assertIsNone(mic.stream)
        fake_pyaudio.PyAudio.return_value.terminate.assert_called()


@unittest.skipIf(paWASAPI is None, "WASAPI is only available on Windows")
class MicRecorderChoiceTests(unittest.TestCase):
    def _record(self, recorder_class, device: dict):
        with patch(f"{_RECORDER}._DownmixedMicrophone") as downmixed, \
                patch(f"{_RECORDER}.Microphone") as plain:
            downmixed.return_value = _opened_source()
            plain.return_value = _opened_source()
            if recorder_class is SelectedMicEnergyAndAudioRecorder:
                recorder_class(device=device, energy_threshold=300, dynamic_energy_threshold=False, phrase_time_limit=3)
            else:
                recorder_class(device=device)
        return downmixed, plain

    def test_wasapi_stereo_mic_is_opened_with_its_channels_and_downmixed(self) -> None:
        device = {"index": 5, "defaultSampleRate": 48000, "maxInputChannels": 2, "hostApiType": paWASAPI}
        downmixed, plain = self._record(SelectedMicEnergyAndAudioRecorder, device)
        downmixed.assert_called_once_with(native_channels=2, device_index=5, sample_rate=48000)
        plain.assert_not_called()

    def test_wasapi_stereo_mic_is_downmixed_in_vad_mode_too(self) -> None:
        device = {"index": 5, "defaultSampleRate": 48000, "maxInputChannels": 2, "hostApiType": paWASAPI}
        downmixed, plain = self._record(SelectedMicVadRecorder, device)
        downmixed.assert_called_once_with(native_channels=2, device_index=5, sample_rate=48000)
        plain.assert_not_called()

    def test_wasapi_mono_mic_is_opened_as_before(self) -> None:
        device = {"index": 5, "defaultSampleRate": 48000, "maxInputChannels": 1, "hostApiType": paWASAPI}
        downmixed, plain = self._record(SelectedMicEnergyAndAudioRecorder, device)
        downmixed.assert_not_called()
        plain.assert_called_once_with(device_index=5, sample_rate=48000)

    def test_mme_stereo_mic_is_opened_as_before(self) -> None:
        device = {"index": 1, "defaultSampleRate": 44100, "maxInputChannels": 2, "hostApiType": _PA_MME}
        downmixed, plain = self._record(SelectedMicEnergyAndAudioRecorder, device)
        downmixed.assert_not_called()
        plain.assert_called_once_with(device_index=1, sample_rate=44100)

    def test_device_without_host_api_type_is_opened_as_before(self) -> None:
        device = {"index": 1, "defaultSampleRate": 44100, "maxInputChannels": 2}
        downmixed, plain = self._record(SelectedMicEnergyAndAudioRecorder, device)
        downmixed.assert_not_called()
        plain.assert_called_once_with(device_index=1, sample_rate=44100)


class MicDeviceHostApiTypeTests(unittest.TestCase):
    def test_mic_device_carries_its_host_api_type(self) -> None:
        device = {"index": 3, "name": "Line 1 (Virtual Audio Cable)", "maxInputChannels": 2}
        entry = _micDeviceWithHostApiType(device, {"type": 13, "name": "Windows WASAPI"})
        self.assertEqual(entry["hostApiType"], 13)
        self.assertEqual(entry["name"], "Line 1 (Virtual Audio Cable)")
        self.assertNotIn("hostApiType", device)


if __name__ == "__main__":
    unittest.main()
