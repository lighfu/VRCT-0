"""初回起動時の既定値 (リリースチャンネル・UI 言語) のテスト。

NSIS インストーラーが置いていた installer_language.txt と、起動のたびに
チャンネルを VERSION から上書きする処理をやめた。代わりに config.json が
無いときの既定値を、チャンネルは VERSION から、UI 言語は OS の表示言語から
決める。保存済みの値は上書きしない。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config as config_module
from config import Config


def _isolated_config() -> Config:
    """シングルトン (Config.__new__) を通らない、独立した Config を作る。"""
    instance = object.__new__(Config)
    instance.init_config()
    return instance


def _cancel_timer(instance: Config) -> None:
    timer = getattr(instance, "_timer", None)
    if timer is not None:
        timer.cancel()


class UiLanguageForLocaleTests(unittest.TestCase):
    def test_known_locales(self) -> None:
        cases = {
            "ja_JP": "ja",
            "ko_KR": "ko",
            "zh_TW": "zh-Hant",
            "zh_HK": "zh-Hant",
            "zh_CN": "zh-Hans",
            "zh_SG": "zh-Hans",
            "en_US": "en",
            "de_DE": "en",
            "": "en",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(config_module.uiLanguageForLocale(name), expected)


class FirstRunDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.json"

    def _load(self, saved):
        if saved is not None:
            self.config_path.write_text(json.dumps(saved), encoding="utf-8")
        with patch.object(config_module, "osUiLanguage", return_value="ja"):
            instance = _isolated_config()
        instance._PATH_CONFIG = str(self.config_path)
        instance.load_config()
        self.addCleanup(_cancel_timer, instance)
        return instance

    def test_ui_language_defaults_to_the_os_language(self) -> None:
        self.assertEqual(self._load(None).UI_LANGUAGE, "ja")

    def test_saved_ui_language_wins(self) -> None:
        self.assertEqual(self._load({"UI_LANGUAGE": "ko"}).UI_LANGUAGE, "ko")

    def test_channel_defaults_to_the_channel_of_this_version(self) -> None:
        instance = self._load(None)
        self.assertEqual(
            instance.SELECTED_RELEASE_CHANNEL,
            Config._channelForVersion(instance.VERSION),
        )

    def test_saved_channel_is_not_overwritten(self) -> None:
        self.assertEqual(self._load({"SELECTED_RELEASE_CHANNEL": "stable"}).SELECTED_RELEASE_CHANNEL, "stable")
        self.assertEqual(self._load({"SELECTED_RELEASE_CHANNEL": "beta"}).SELECTED_RELEASE_CHANNEL, "beta")

    def test_channel_for_version(self) -> None:
        self.assertEqual(Config._channelForVersion("3.5.1"), "stable")
        self.assertEqual(Config._channelForVersion("3.5.1-beta.1"), "beta")
        self.assertEqual(Config._channelForVersion("3.5.1-rc.2"), "beta")
