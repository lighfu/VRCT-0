"""/run/shutdown のあと、サイドカーが自分で終わることのテスト (GPU 高速化パックの最終修正 C1)。

画面は /run/shutdown を送って 2 秒後に閉じる (または起動し直す)。普通は Tauri
(tauri-plugin-shell) がそこでサイドカーを止める。ところが画面からの再起動が
RunEvent::Exit を通らなかったとき、誰もサイドカーを止めず、watchdog も
controller.shutdown() で止まっているので、前のサイドカーが RAM と GPU 高速化パックの DLL を
掴んだまま残り続けた (2026-09-24 の実機確認)。/run/shutdown の処理が、止める処理の
あとで自分の終わりを予約していれば、親が止めなくても終わる。

os._exit は本当に呼ばないよう、必ず差し替える。
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import mainloop as mainloop_module


class _FakeTimer:
    """threading.Timer の代わり。作られた順と start / cancel を events に記録する。"""

    def __init__(self, events, seconds, function, args=()):
        self.events = events
        self.seconds = seconds
        self.function = function
        self.args = args
        self.daemon = False

    def start(self):
        self.events.append(("start", self.seconds, self.args, self.daemon))

    def cancel(self):
        self.events.append(("cancel", self.seconds))


class ShutdownSelfExitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = []
        self.timers = []
        self.controller = MagicMock()
        self.controller.shutdown.side_effect = self._shutdown

        def make_timer(seconds, function, args=()):
            timer = _FakeTimer(self.events, seconds, function, args)
            self.timers.append(timer)
            return timer

        for target, value in (("controller", self.controller), ("Timer", make_timer)):
            patcher = patch.object(mainloop_module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        exit_patcher = patch.object(mainloop_module.os, "_exit")
        self.mock_exit = exit_patcher.start()
        self.addCleanup(exit_patcher.stop)

    def _shutdown(self, *args, **kwargs):
        self.events.append("shutdown")
        return {"status": 200, "result": True}

    def test_the_endpoint_uses_the_self_exiting_handler(self) -> None:
        self.assertIs(mainloop_module.mapping["/run/shutdown"]["variable"], mainloop_module.shutdownThenExit)

    def test_exit_is_scheduled_after_the_shutdown_work(self) -> None:
        result = mainloop_module.shutdownThenExit(None)

        self.assertEqual(result, {"status": 200, "result": True})
        grace = mainloop_module._WATCHDOG_GRACE_PERIOD_SEC
        delay = mainloop_module._SHUTDOWN_SELF_EXIT_DELAY_SEC
        # 止める処理が詰まっても必ず終わる上限を先に仕掛け、終わったら取り消して、短い時計に替える。
        self.assertEqual(
            self.events,
            [("start", grace, (1,), True), "shutdown", ("cancel", grace), ("start", delay, (0,), True)],
        )
        for timer in self.timers:
            self.assertIs(timer.function, self.mock_exit)
        self.mock_exit.assert_not_called()

    def test_exit_is_scheduled_even_when_the_shutdown_work_fails(self) -> None:
        self.controller.shutdown.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            mainloop_module.shutdownThenExit(None)

        delay = mainloop_module._SHUTDOWN_SELF_EXIT_DELAY_SEC
        self.assertEqual(self.events[-1], ("start", delay, (0,), True))

    def test_delay_leaves_time_for_the_frontend_to_stop_the_sidecar_first(self) -> None:
        # 画面は /run/shutdown から 2 秒後に閉じる・起動し直す。普通はそこで Tauri が止めるので、
        # 自分で終わるのはそのあと (親が止めなかったときだけ) にする。
        self.assertGreater(mainloop_module._SHUTDOWN_SELF_EXIT_DELAY_SEC, 2)


class ShutdownSelfExitRealTimerTests(unittest.TestCase):
    def test_the_real_timer_calls_exit_with_zero(self) -> None:
        controller = MagicMock()
        controller.shutdown.return_value = {"status": 200, "result": True}
        called = threading.Event()
        with patch.object(mainloop_module, "controller", controller), \
                patch.object(mainloop_module, "_SHUTDOWN_SELF_EXIT_DELAY_SEC", 0.05), \
                patch.object(mainloop_module.os, "_exit", side_effect=lambda code: called.set()) as mock_exit:
            mainloop_module.shutdownThenExit(None)
            self.assertTrue(called.wait(2.0))
            time.sleep(0.05)
        mock_exit.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
