"""起動直後の `/run/feed_watchdog` が 500 にならないことのテスト。

背景 (2026-09-24 の起動テストで発覚):

UI はサイドカーを spawn した直後から `/run/feed_watchdog` を送るが、
その時点ではメインスレッドの `controller.init()` → `Model.init()` が
まだ走っている。`Model.init()` は開始時に `_init_failed = True` を立てて
「成功が証明されるまで失敗扱い」にするので、別スレッドから来た
`ensure_initialized()` は初期化中にもかかわらず何もせず戻り、
まだ作られていない `self.watchdog` に触って AttributeError になっていた。

watchdog は `Model.init()` で作られ `startWatchdog()` で動き出すので、
それより前の feed には意味がない。何もせずに成功扱いで戻す。
"""

import unittest
from unittest.mock import MagicMock, patch

import controller as controller_module
import model as model_module
from controller import Controller
from model import Model


def _modelWithInitInProgress() -> Model:
    # Model.init() の冒頭を通過した直後 (watchdog 生成前) の状態を再現する。
    model = object.__new__(Model)
    model._inited = False
    model._init_failed = True
    return model


class FeedWatchdogBeforeInitTests(unittest.TestCase):
    def test_feed_during_init_does_not_raise_or_arm_dump(self) -> None:
        model = _modelWithInitInProgress()
        with patch.object(model_module.faulthandler, "dump_traceback_later") as mock_arm:
            model.feedWatchdog()
        mock_arm.assert_not_called()

    def test_feed_after_init_feeds_the_watchdog(self) -> None:
        model = object.__new__(Model)
        model._inited = True
        model._init_failed = False
        model.watchdog = MagicMock()
        model.watchdog.interval = 20
        with patch.object(model_module.faulthandler, "dump_traceback_later") as mock_arm:
            model.feedWatchdog()
        model.watchdog.feed.assert_called_once()
        mock_arm.assert_called_once()

    def test_endpoint_returns_200_while_model_is_initializing(self) -> None:
        with patch.object(controller_module, "model", _modelWithInitInProgress()):
            self.assertEqual(Controller.feedWatchdog(), {"status": 200, "result": True})


if __name__ == "__main__":
    unittest.main()
