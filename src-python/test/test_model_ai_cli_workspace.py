"""AI CLI の作業フォルダは PATH_DATA に、プロンプトの設定は PATH_APP から読む。"""

import os
import unittest
from unittest.mock import MagicMock, patch

from config import config
from model import model


class AiCliWorkspaceTests(unittest.TestCase):
    def _call(self, method_name: str, *args):
        translator = MagicMock()
        translator.checkAiCliClient.return_value = True
        with patch.object(model, "ensure_initialized"), \
                patch.object(model, "translator", translator, create=True):
            getattr(model, method_name)(*args)
        return translator.checkAiCliClient.call_args.kwargs

    def test_set_tool_passes_the_data_workspace(self) -> None:
        kwargs = self._call("setTranslatorAiCliTool", "claude")
        self.assertEqual(kwargs["root_path"], config.PATH_APP)
        self.assertEqual(kwargs["workspace"], os.path.join(config.PATH_DATA, "ai_cli_workspace"))

    def test_authentication_passes_the_data_workspace(self) -> None:
        kwargs = self._call("authenticationTranslatorAiCli")
        self.assertEqual(kwargs["root_path"], config.PATH_APP)
        self.assertEqual(kwargs["workspace"], os.path.join(config.PATH_DATA, "ai_cli_workspace"))


if __name__ == "__main__":
    unittest.main()
