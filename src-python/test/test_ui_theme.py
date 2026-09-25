"""テーマ (設定の「テーマ」タブ) の保存と背景画像のテスト。

色や背景の中身は UI (src-ui/logics/theme) が検査して直す。ここでは
config.UI_THEME が形と大きさだけを見ること、背景画像が data URL で
保存・読み込みでき、使われなくなった画像が消えることを確かめる。
"""

import base64
import os
import tempfile
import unittest
from unittest import mock

from config import config, _ui_theme_validator, UI_THEME_MAX_CUSTOM_THEMES
from controller import Controller
from models import ui_theme_images

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def _dataUrl(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class TestUiThemeValidator(unittest.TestCase):
    def test_accepts_selected_id_and_custom_themes(self) -> None:
        value = {"selected_id": "custom_abc", "custom_themes": [{"id": "custom_abc", "name": "A"}]}
        self.assertEqual(_ui_theme_validator(value, None), value)

    def test_drops_unknown_top_level_keys(self) -> None:
        value = {"selected_id": "preset_standard", "custom_themes": [], "extra": 1}
        self.assertEqual(
            _ui_theme_validator(value, None),
            {"selected_id": "preset_standard", "custom_themes": []},
        )

    def test_rejects_broken_shapes(self) -> None:
        for value in (
            None,
            "preset_standard",
            {"selected_id": "", "custom_themes": []},
            {"selected_id": "x" * 65, "custom_themes": []},
            {"selected_id": 1, "custom_themes": []},
            {"selected_id": "preset_standard", "custom_themes": {}},
            {"selected_id": "preset_standard", "custom_themes": ["not a dict"]},
        ):
            with self.subTest(value=value):
                self.assertIsNone(_ui_theme_validator(value, None))

    def test_rejects_too_many_themes(self) -> None:
        themes = [{"id": f"custom_{i}"} for i in range(UI_THEME_MAX_CUSTOM_THEMES + 1)]
        self.assertIsNone(_ui_theme_validator({"selected_id": "preset_standard", "custom_themes": themes}, None))

    def test_rejects_huge_payload(self) -> None:
        themes = [{"id": "custom_a", "name": "x" * (600 * 1024)}]
        self.assertIsNone(_ui_theme_validator({"selected_id": "preset_standard", "custom_themes": themes}, None))


class TestSetUiTheme(unittest.TestCase):
    def setUp(self) -> None:
        self.original = config.UI_THEME

    def tearDown(self) -> None:
        config.UI_THEME = self.original

    def test_set_and_get_round_trip(self) -> None:
        value = {"selected_id": "custom_a", "custom_themes": [{"id": "custom_a", "name": "夜"}]}
        response = Controller.setUiTheme(value)
        self.assertEqual(response, {"status": 200, "result": value})
        self.assertEqual(Controller.__new__(Controller).getUiTheme()["result"], value)

    def test_invalid_value_keeps_previous_and_returns_400(self) -> None:
        response = Controller.setUiTheme({"selected_id": ""})
        self.assertEqual(response["status"], 400)
        self.assertEqual(config.UI_THEME, self.original)


class TestUiThemeImages(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.image_dir = os.path.join(self.tmp.name, ui_theme_images.IMAGE_DIR_NAME)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_load_each_type(self) -> None:
        for mime, data in (("image/png", _PNG), ("image/jpeg", _JPEG), ("image/webp", _WEBP)):
            with self.subTest(mime=mime):
                data_url = _dataUrl(mime, data)
                ui_theme_images.saveImage(self.image_dir, "img_1", data_url)
                self.assertEqual(ui_theme_images.loadImage(self.image_dir, "img_1"), data_url)
                # 同じ id で種類を変えたら、古いほうは残さない。
                self.assertEqual(len(os.listdir(self.image_dir)), 1)

    def test_load_missing_returns_none(self) -> None:
        self.assertIsNone(ui_theme_images.loadImage(self.image_dir, "img_missing"))

    def test_rejects_bad_input(self) -> None:
        cases = (
            ("../evil", _dataUrl("image/png", _PNG)),
            ("img_1", "not a data url"),
            ("img_1", _dataUrl("image/svg+xml", b"<svg/>")),
            ("img_1", _dataUrl("image/png", _JPEG)),
            ("img_1", "data:image/png;base64,@@@@"),
        )
        for image_id, data_url in cases:
            with self.subTest(image_id=image_id, data_url=data_url[:30]):
                with self.assertRaises(ValueError):
                    ui_theme_images.saveImage(self.image_dir, image_id, data_url)
        self.assertFalse(os.path.isdir(self.image_dir) and os.listdir(self.image_dir))

    def test_rejects_too_large_image(self) -> None:
        with mock.patch.object(ui_theme_images, "MAX_IMAGE_BYTES", 8):
            with self.assertRaises(ValueError):
                ui_theme_images.saveImage(self.image_dir, "img_1", _dataUrl("image/png", _PNG))

    def test_remove_unused_images_keeps_referenced_ones(self) -> None:
        ui_theme_images.saveImage(self.image_dir, "img_keep", _dataUrl("image/png", _PNG))
        ui_theme_images.saveImage(self.image_dir, "img_drop", _dataUrl("image/webp", _WEBP))
        with open(os.path.join(self.image_dir, "img_keep.png.tmp"), "wb") as fp:
            fp.write(b"half written")
        ui_theme = {
            "selected_id": "custom_a",
            "custom_themes": [
                {"id": "custom_a", "backdrop": {"type": "image", "image": {"id": "img_keep"}}},
                {"id": "custom_b", "backdrop": {"type": "solid"}},
                {"id": "custom_c", "backdrop": {"image": {"id": "../evil"}}},
            ],
        }
        used = ui_theme_images.usedImageIds(ui_theme)
        self.assertEqual(used, {"img_keep"})
        removed = ui_theme_images.removeUnusedImages(self.image_dir, used)
        self.assertEqual(sorted(removed), ["img_drop.webp", "img_keep.png.tmp"])
        self.assertEqual(os.listdir(self.image_dir), ["img_keep.png"])

    def test_controller_endpoints(self) -> None:
        with mock.patch.object(Controller, "_uiThemeImageDir", staticmethod(lambda: self.image_dir)):
            data_url = _dataUrl("image/webp", _WEBP)
            saved = Controller.saveUiThemeImage({"image_id": "img_1", "data_url": data_url})
            self.assertEqual(saved, {"status": 200, "result": {"image_id": "img_1"}})
            loaded = Controller.loadUiThemeImage("img_1")
            self.assertEqual(loaded, {"status": 200, "result": {"image_id": "img_1", "data_url": data_url}})

            broken = Controller.saveUiThemeImage({"image_id": "img_2", "data_url": "data:image/png;base64,AAAA"})
            self.assertEqual(broken["status"], 400)
            self.assertEqual(broken["result"]["error_code"], "UI_THEME_IMAGE_SAVE_FAILED")
            self.assertEqual(Controller.saveUiThemeImage(None)["status"], 400)
            self.assertEqual(Controller.loadUiThemeImage("../x")["status"], 400)


if __name__ == "__main__":
    unittest.main()
