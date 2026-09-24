"""models/transcription/transcription_sensevoice.py と、SenseVoice の
言語対応表・エンジン一覧・コントローラーへの組み込みのテスト。"""

import hashlib
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from models.transcription import transcription_sensevoice as sv
from models.transcription.transcription_languages import transcription_lang


class TestSenseVoiceLanguages(unittest.TestCase):
    def test_only_supported_languages_have_a_sensevoice_code(self) -> None:
        self.assertEqual(transcription_lang["Japanese"]["Japan"]["SenseVoice"], "ja")
        self.assertEqual(transcription_lang["English"]["United States"]["SenseVoice"], "en")
        self.assertEqual(transcription_lang["Korean"]["South Korea"]["SenseVoice"], "ko")
        self.assertEqual(transcription_lang["Chinese Simplified"]["China"]["SenseVoice"], "zh")
        self.assertEqual(transcription_lang["Chinese Traditional"]["Hong Kong"]["SenseVoice"], "yue")
        self.assertNotIn("SenseVoice", transcription_lang["French"]["France"])

    def test_engine_list_includes_engines_missing_from_the_first_language(self) -> None:
        from config import config
        self.assertIn("SenseVoice", list(config.SELECTABLE_TRANSCRIPTION_ENGINE_LIST))


class _FakeFiles:
    """_FILES を小さな偽物に差し替え、取得を偽の downloadFile で行う。"""

    def __init__(self, test: unittest.TestCase, contents: dict) -> None:
        self.contents = contents
        files = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in contents.items()}
        for target in (
            patch.object(sv, "_FILES", files),
            patch.object(sv, "_MODEL_FILE", "model.int8.onnx"),
        ):
            target.start()
            test.addCleanup(target.stop)
        hub = MagicMock()
        hub.hf_hub_url.side_effect = lambda repo, filename, revision=None: f"https://hf/{repo}@{revision}/{filename}"
        modules = patch.dict(sys.modules, {"huggingface_hub": hub})
        modules.start()
        test.addCleanup(modules.stop)
        self.hub = hub

    def serve(self, overrides: dict = None):
        overrides = overrides or {}

        def _download(url, path, func=None):
            name = url.rsplit("/", 1)[1]
            data = overrides.get(name, self.contents[name])
            if data is None:
                return False
            with open(path, "wb") as f:
                f.write(data)
            if func:
                func(1.0)
            return True
        return _download


class TestSenseVoiceWeight(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.fake = _FakeFiles(self, {"model.int8.onnx": b"model-bytes", "tokens.txt": b"tokens"})

    def _download(self, overrides=None, progress=None, ended=None):
        with patch.object(sv, "downloadFile", side_effect=self.fake.serve(overrides)):
            return sv.downloadSenseVoiceWeight(self.root, progress, ended)

    def test_download_verifies_and_installs_both_files_from_the_pinned_revision(self) -> None:
        progress = MagicMock()
        ended = MagicMock()

        self.assertTrue(self._download(progress=progress, ended=ended))

        self.assertTrue(sv.checkSenseVoiceWeight(self.root))
        progress.assert_called_once_with(1.0)  # 進み具合はモデル本体の分だけ
        ended.assert_called_once()
        revisions = {c.kwargs["revision"] for c in self.fake.hub.hf_hub_url.call_args_list}
        self.assertEqual(revisions, {sv.SENSEVOICE_REVISION})

    def test_a_hash_mismatch_leaves_nothing_installed(self) -> None:
        ended = MagicMock()

        self.assertFalse(self._download({"model.int8.onnx": b"model-byteX"}, ended=ended))

        directory = os.path.join(self.root, "weights", "sensevoice")
        self.assertFalse(os.path.exists(os.path.join(directory, "model.int8.onnx")))
        self.assertFalse(os.path.exists(os.path.join(directory, "model.int8.onnx.tmp")))
        self.assertFalse(sv.checkSenseVoiceWeight(self.root))
        ended.assert_called_once()

    def test_an_interrupted_download_is_not_reported_as_installed(self) -> None:
        """途中で終わった取得は一時ファイルにしか残らない。"""
        directory = os.path.join(self.root, "weights", "sensevoice")
        os.makedirs(directory)
        with open(os.path.join(directory, "model.int8.onnx.tmp"), "wb") as f:
            f.write(b"model")
        with open(os.path.join(directory, "tokens.txt"), "wb") as f:
            f.write(b"tokens")

        self.assertFalse(sv.checkSenseVoiceWeight(self.root))

    def test_files_changed_after_verification_are_not_trusted(self) -> None:
        self.assertTrue(self._download())
        path = os.path.join(self.root, "weights", "sensevoice", "tokens.txt")
        with open(path, "wb") as f:
            f.write(b"tokenX")
        stat = os.stat(path)
        os.utime(path, (stat.st_atime, stat.st_mtime + 10))  # 書き換えた時刻が記録と違う

        self.assertFalse(sv.checkSenseVoiceWeight(self.root))

    def test_end_callback_runs_even_when_setup_raises(self) -> None:
        ended = MagicMock()
        with patch.object(sv, "os_makedirs", side_effect=OSError("disk")), \
                patch.object(sv, "errorLogging"):
            self.assertFalse(sv.downloadSenseVoiceWeight(self.root, None, ended))
        ended.assert_called_once()

    def test_check_does_not_import_sherpa_onnx(self) -> None:
        self.assertTrue(self._download())
        with patch.object(sv, "loadSenseVoiceRuntime") as load:
            self.assertTrue(sv.checkSenseVoiceWeight(self.root))
        load.assert_not_called()


class TestSenseVoiceRecognizerSharing(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(sv._recognizers, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mic_and_speaker_share_one_auto_recognizer_and_it_is_freed_after_both_return_it(self) -> None:
        runtime = MagicMock()
        with patch.object(sv, "loadSenseVoiceRuntime", return_value=runtime):
            mic = sv.acquireSenseVoiceRecognizer("root")
            speaker = sv.acquireSenseVoiceRecognizer("root")

        self.assertIs(mic, speaker)
        runtime.OfflineRecognizer.from_sense_voice.assert_called_once()
        self.assertEqual(runtime.OfflineRecognizer.from_sense_voice.call_args.kwargs["language"], "auto")

        sv.releaseSenseVoiceRecognizer("root")
        self.assertEqual(len(sv._recognizers), 1)  # スピーカーがまだ使っている
        sv.releaseSenseVoiceRecognizer("root")
        self.assertEqual(sv._recognizers, {})  # ほかのエンジンに戻したら残さない
        sv.releaseSenseVoiceRecognizer("root")  # 余分に返しても壊れない

    def test_raises_a_runtime_error_when_sherpa_onnx_cannot_be_loaded(self) -> None:
        with patch.object(sv, "loadSenseVoiceRuntime", return_value=None):
            with self.assertRaises(sv.SenseVoiceRuntimeError):
                sv.acquireSenseVoiceRecognizer("root")
        self.assertEqual(sv._recognizers, {})

    def test_decode_strips_the_language_tag(self) -> None:
        recognizer = MagicMock()
        recognizer.create_stream.return_value.result = MagicMock(text="hi", lang="<|en|>")
        self.assertEqual(sv.SenseVoiceRecognizer(recognizer).decode(MagicMock(), 16000), ("hi", "en"))


class TestAudioTranscriberReleasesSenseVoice(unittest.TestCase):
    def test_close_returns_the_shared_recognizer_once(self) -> None:
        from models.transcription import transcription_transcriber as tt

        class _Source:
            SAMPLE_RATE = 16000
            SAMPLE_WIDTH = 2
            channels = 1

        with patch.object(tt, "checkSenseVoiceWeight", return_value=True), \
                patch.object(tt, "acquireSenseVoiceRecognizer", return_value=MagicMock()) as acquire, \
                patch.object(tt, "releaseSenseVoiceRecognizer") as release:
            transcriber = tt.AudioTranscriber(False, _Source(), 3, 10, "SenseVoice", root="root")
            transcriber.close()
            transcriber.close()

        acquire.assert_called_once_with("root")
        release.assert_called_once_with("root")
        self.assertIsNone(transcriber._resolve_provider())


class TestDownloadSkipsVerifiedFiles(unittest.TestCase):
    def test_only_the_missing_file_is_downloaded_again(self) -> None:
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        fake = _FakeFiles(self, {"model.int8.onnx": b"model-bytes", "tokens.txt": b"tokens"})
        with patch.object(sv, "downloadFile", side_effect=fake.serve({"tokens.txt": None})):
            self.assertFalse(sv.downloadSenseVoiceWeight(root))
        with patch.object(sv, "downloadFile", side_effect=fake.serve()) as download:
            self.assertTrue(sv.downloadSenseVoiceWeight(root))
        fetched = [os.path.basename(c.args[1]) for c in download.call_args_list]
        self.assertEqual(fetched, ["tokens.txt.tmp"])


class TestControllerSenseVoiceDownload(unittest.TestCase):
    """ダウンロード完了でエンジンが使える状態になり、UI の重み一覧にも反映される。"""

    def setUp(self) -> None:
        from config import config
        self.config = config
        self._original_status = dict(config._SELECTABLE_TRANSCRIPTION_ENGINE_STATUS)
        self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"] = False

    def tearDown(self) -> None:
        self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS = self._original_status

    def _download(self, weight_ok: bool, runtime_ok: bool = True):
        from controller import Controller
        calls = []
        run_mapping = {
            "download_progress_sensevoice_weight": "/run/download_progress_sensevoice_weight",
            "downloaded_sensevoice_weight": "/run/downloaded_sensevoice_weight",
            "error_sensevoice_weight": "/run/error_sensevoice_weight",
            "selectable_transcription_engines": "/run/selectable_transcription_engines",
        }
        download = Controller.DownloadSenseVoice(run_mapping, "sensevoice-small", lambda *a: calls.append(a))
        with patch("controller.model") as mock_model:
            mock_model.checkSenseVoiceModelWeight.return_value = weight_ok
            mock_model.loadSenseVoiceRuntime.return_value = runtime_ok
            download.downloaded()
        return calls

    def test_success_enables_the_engine(self) -> None:
        calls = self._download(True)

        self.assertEqual(calls[-1], (200, "/run/downloaded_sensevoice_weight", "sensevoice-small"))
        self.assertTrue(self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"])
        # 画面のエンジンの選択肢にもすぐ反映する
        engines = [c for c in calls if c[1] == "/run/selectable_transcription_engines"]
        self.assertEqual(len(engines), 1)
        self.assertIn("SenseVoice", engines[0][2])

    def test_failure_reports_a_download_error(self) -> None:
        calls = self._download(False)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "/run/error_sensevoice_weight")
        self.assertEqual(calls[0][2]["error_code"], "WEIGHT_SENSEVOICE_DOWNLOAD")
        self.assertFalse(self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"])

    def test_runtime_failure_is_reported_separately_and_weights_stay_downloaded(self) -> None:
        """sherpa_onnx が読み込めないのは取得の失敗ではない (取り直させない)。"""
        calls = self._download(True, runtime_ok=False)

        endpoints = [c[1] for c in calls]
        self.assertIn("/run/downloaded_sensevoice_weight", endpoints)
        errors = [c[2]["error_code"] for c in calls if c[1] == "/run/error_sensevoice_weight"]
        self.assertEqual(errors, ["SENSEVOICE_RUNTIME_UNAVAILABLE"])
        self.assertFalse(self.config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS["SenseVoice"])

    def test_weight_dict_reflects_the_files_not_the_runtime(self) -> None:
        from controller import Controller
        with patch("controller.model") as mock_model:
            mock_model.checkSenseVoiceModelWeight.return_value = True
            result = Controller.getSelectableSenseVoiceWeightTypeDict()["result"]
        self.assertEqual(result, {"sensevoice-small": True})


if __name__ == "__main__":
    unittest.main()
