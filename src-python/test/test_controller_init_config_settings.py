"""起動時の一括設定取得 (`Controller.updateConfigSettings`) の堅牢性テスト。

背景 (バックエンド再評価 2026-09-14 P-1):

`Controller.init()` の最終行が `updateConfigSettings()` を呼び、`init_mapping`
(= `/get/data/` 前方一致の全エンドポイント) を全て舐めてから
`/run/initialization_complete` を送る。UIのローディング画面はこの応答で解除
されるため、getter 呼び出しに try/except が無く、1 つでも例外を投げると
`init()` がそこで死に、`/run/initialization_complete` が永久に送られず UI が
ローディング画面のまま固まる、という弱点があった。

本テストはこの回帰を防ぐ。
"""

import unittest

from controller import Controller
from mainloop import init_mapping, mapping


class TestInitMappingCollectsAllGetData(unittest.TestCase):
    def test_every_get_data_endpoint_is_collected_at_startup(self) -> None:
        get_data_keys = {k for k in mapping if k.startswith("/get/data/")}
        self.assertEqual(get_data_keys - set(init_mapping), set())

    def test_update_endpoints_are_gone(self) -> None:
        for endpoint in (
            "/get/data/available_releases",
            "/run/update_software",
            "/run/update_cuda_software",
        ):
            self.assertNotIn(endpoint, mapping)


class TestUpdateConfigSettingsIsolatesFailures(unittest.TestCase):
    def _makeController(self, init_mapping_override: dict) -> tuple:
        controller = Controller.__new__(Controller)
        controller.init_mapping = init_mapping_override
        controller.run_mapping = {"initialization_complete": "/run/initialization_complete"}
        sent = []
        controller.run = lambda status, endpoint, result: sent.append((status, endpoint, result))
        return controller, sent

    def test_a_raising_getter_does_not_abort_initialization(self) -> None:
        """1 つの getter が例外を投げても、残りを集めて完了通知まで到達すること。"""
        def ok(_data):
            return {"status": 200, "result": "fine"}

        def boom(_data):
            raise RuntimeError("getter exploded")

        controller, sent = self._makeController({
            "/get/data/good": {"status": True, "variable": ok},
            "/get/data/bad": {"status": True, "variable": boom},
            "/get/data/good2": {"status": True, "variable": ok},
        })

        controller.updateConfigSettings()

        self.assertEqual(len(sent), 1, "initialization_complete が送られていない")
        status, endpoint, settings = sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(endpoint, "/run/initialization_complete")
        self.assertEqual(settings["/get/data/good"], "fine")
        self.assertEqual(settings["/get/data/good2"], "fine")
        self.assertIsNone(settings["/get/data/bad"], "失敗した項目は None で埋めること")

    def test_a_getter_returning_a_non_dict_is_also_isolated(self) -> None:
        """dict 以外を返す getter (=`.get` が無い) でも落ちないこと。"""
        controller, sent = self._makeController({
            "/get/data/weird": {"status": True, "variable": lambda _data: None},
        })

        controller.updateConfigSettings()

        self.assertEqual(len(sent), 1)
        self.assertIsNone(sent[0][2]["/get/data/weird"])


if __name__ == "__main__":
    unittest.main()
