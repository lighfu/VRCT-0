import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from models.transliteration import transliteration_dictionary as sd


def _zipBytes(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def _fakeResponse(body: bytes) -> MagicMock:
    response = MagicMock()
    response.headers = {"content-length": str(len(body))}
    response.iter_content.return_value = [body[i:i + 1000] for i in range(0, len(body), 1000)]
    response.raise_for_status.return_value = None
    return response


class DownloadSudachiFullDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.payload = b"fake dictionary" * 100
        self.archive = _zipBytes(sd.SUDACHI_FULL_DICT_MEMBER, self.payload)

    def test_extracts_dictionary_and_reports_progress(self) -> None:
        progress = []
        end = MagicMock()
        with patch.object(sd, "SUDACHI_FULL_DICT_SHA256", hashlib.sha256(self.archive).hexdigest()), \
             patch.object(sd, "requests_get", return_value=_fakeResponse(self.archive)):
            ok = sd.downloadSudachiFullDict(self.root, callback=progress.append, end_callback=end)
        self.assertTrue(ok)
        with open(sd.sudachiFullDictPath(self.root), "rb") as f:
            self.assertEqual(f.read(), self.payload)
        self.assertTrue(sd.checkSudachiFullDict(self.root))
        self.assertAlmostEqual(progress[-1], 1.0)
        end.assert_called_once()
        self.assertFalse([n for n in os.listdir(os.path.dirname(sd.sudachiFullDictPath(self.root))) if n.endswith(".zip")])

    def test_hash_mismatch_leaves_no_dictionary(self) -> None:
        end = MagicMock()
        with patch.object(sd, "SUDACHI_FULL_DICT_SHA256", "0" * 64), \
             patch.object(sd, "requests_get", return_value=_fakeResponse(self.archive)), \
             patch.object(sd, "sleep"):
            ok = sd.downloadSudachiFullDict(self.root, end_callback=end)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(sd.sudachiFullDictPath(self.root)))
        self.assertFalse(sd.checkSudachiFullDict(self.root))
        end.assert_called_once()

    def test_network_error_is_retried_then_reported(self) -> None:
        with patch.object(sd, "requests_get", side_effect=OSError("offline")) as mock_get, \
             patch.object(sd, "sleep"):
            ok = sd.downloadSudachiFullDict(self.root)
        self.assertFalse(ok)
        self.assertEqual(mock_get.call_count, sd._DOWNLOAD_MAX_ATTEMPTS)

    def test_skips_download_when_already_verified(self) -> None:
        with patch.object(sd, "checkSudachiFullDict", return_value=True), \
             patch.object(sd, "requests_get") as mock_get:
            self.assertTrue(sd.downloadSudachiFullDict(self.root))
        mock_get.assert_not_called()

    def test_check_is_false_when_file_is_absent(self) -> None:
        self.assertFalse(sd.checkSudachiFullDict(self.root))
