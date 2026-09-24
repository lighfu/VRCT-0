"""データの置き場所 (PATH_DATA) と同梱ファイルの場所 (PATH_APP) のテスト。

Velopack で入れた版では本体 (VRCT-0.exe) が <導入先>\\data を環境変数
VRCT_DATA_DIR で渡す。設定・ログ・モデルはそこへ、同梱ファイルは
実行ファイルのフォルダから読む。開発中は両方とも今までどおり。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils
from config import Config

SRC_PYTHON = Path(__file__).resolve().parents[1]


def _isolated_config() -> Config:
    instance = object.__new__(Config)
    instance.init_config()
    return instance


def _env_without_data_dir() -> dict:
    return {k: v for k, v in os.environ.items() if k != utils.DATA_DIR_ENV}


class DataDirectoryTests(unittest.TestCase):
    def test_env_var_is_used_when_set(self) -> None:
        with patch.dict(os.environ, {utils.DATA_DIR_ENV: r"D:\somewhere\data"}):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"D:\somewhere\data")

    def test_app_dir_is_used_without_env_var(self) -> None:
        with patch.dict(os.environ, _env_without_data_dir(), clear=True):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"C:\app")

    def test_blank_env_var_is_ignored(self) -> None:
        with patch.dict(os.environ, {utils.DATA_DIR_ENV: "  "}):
            self.assertEqual(utils.dataDirectory(r"C:\app"), r"C:\app")


class ConfigPathsTests(unittest.TestCase):
    def test_installed_layout_puts_config_and_logs_in_data(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {utils.DATA_DIR_ENV: data_dir}):
                instance = _isolated_config()
            self.assertEqual(instance.PATH_DATA, data_dir)
            self.assertEqual(instance.PATH_CONFIG, os.path.join(data_dir, "config.json"))
            self.assertEqual(instance.PATH_LOGS, os.path.join(data_dir, "logs"))
            self.assertTrue(os.path.isdir(instance.PATH_LOGS))
        self.assertEqual(Path(instance.PATH_APP), SRC_PYTHON)

    def test_dev_layout_keeps_everything_next_to_the_code(self) -> None:
        with patch.dict(os.environ, _env_without_data_dir(), clear=True):
            instance = _isolated_config()
        self.assertEqual(instance.PATH_DATA, instance.PATH_APP)


class NoPathLocalLeftTests(unittest.TestCase):
    def test_path_local_is_gone_from_the_code(self) -> None:
        offenders = []
        for path in SRC_PYTHON.rglob("*.py"):
            if "test" in path.parts or "__pycache__" in path.parts:
                continue
            if "PATH_LOCAL" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(SRC_PYTHON)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
