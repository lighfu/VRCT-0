"""utils/publish_release.py (VRCT-0 の公開) のテスト。

git や gh を動かす部分は差し替えて、版の比べ方・書き換え・手順の組み立てを確かめる。
pytest の pythonpath は src-python で、そこにも utils.py があるため、
リポジトリ直下の utils/publish_release.py はファイルの場所から読み込む。
"""

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("publish_release", ROOT / "utils" / "publish_release.py")
publish_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_release)


class VersionOrderTests(unittest.TestCase):
    def test_stable_is_newer_than_its_own_beta(self) -> None:
        self.assertTrue(publish_release.isNewer("2026.9.25", "2026.9.25-beta.3"))
        self.assertFalse(publish_release.isNewer("2026.9.25-beta.3", "2026.9.25"))

    def test_later_dates_and_betas_are_newer(self) -> None:
        self.assertTrue(publish_release.isNewer("2026.9.26-beta.1", "2026.9.25"))
        self.assertTrue(publish_release.isNewer("2026.10.1", "2026.9.30"))
        self.assertTrue(publish_release.isNewer("2026.9.25-beta.2", "2026.9.25-beta.1"))
        self.assertFalse(publish_release.isNewer("2026.9.25", "2026.9.25"))

    def test_reads_tags_with_v(self) -> None:
        self.assertEqual(publish_release.parseVersion("v2026.9.25"), publish_release.parseVersion("2026.9.25"))
        self.assertIsNone(publish_release.parseVersion("latest"))

    def test_channel(self) -> None:
        self.assertEqual(publish_release.channelOf("2026.9.25"), "stable")
        self.assertEqual(publish_release.channelOf("2026.9.25-beta.1"), "beta")


class BuildChannelTests(unittest.TestCase):
    def test_rewrites_only_the_setting_line(self) -> None:
        content = '"""説明に BUILD_CHANNEL = "x" と書いてあっても"""\nBUILD_CHANNEL = "beta"  # "stable" | "beta"\n'
        self.assertEqual(
            publish_release.buildChannelContent(content, "stable"),
            '"""説明に BUILD_CHANNEL = "x" と書いてあっても"""\nBUILD_CHANNEL = "stable"  # "stable" | "beta"\n',
        )

    def test_real_file_has_the_setting_line(self) -> None:
        content = (ROOT / publish_release.BUILD_CHANNEL_FILE).read_text(encoding="utf-8")
        publish_release.buildChannelContent(content, "stable")

    def test_missing_line_is_an_error(self) -> None:
        with self.assertRaises(publish_release.ReleaseError):
            publish_release.buildChannelContent("nothing here\n", "beta")


class AssetTests(unittest.TestCase):
    def test_all_required_assets(self) -> None:
        names = ["VRCT-0-win-Setup.exe", "releases.win.json", "VRCT-0-2026.9.25-full.nupkg", "assets.win.json"]
        self.assertEqual(publish_release.missingAssets(names), [])

    def test_reports_missing_setup(self) -> None:
        missing = publish_release.missingAssets(["releases.win.json", "VRCT-0-2026.9.25-full.nupkg"])
        self.assertEqual(len(missing), 1)
        self.assertIn("Setup", missing[0])


class FindRunTests(unittest.TestCase):
    def test_skips_runs_that_existed_before_the_push(self) -> None:
        runs = '[{"databaseId": 1, "headBranch": "v2026.9.25", "url": "old"}, {"databaseId": 2, "headBranch": "v2026.9.25", "url": "new"}]'
        with mock.patch.object(publish_release, "gh", return_value=runs):
            self.assertEqual(publish_release.findRun("v2026.9.25", {1})["url"], "new")
            self.assertEqual(publish_release.listRunIds("v2026.9.25"), {1, 2})


class DryRunTests(unittest.TestCase):
    def test_dry_run_changes_nothing(self) -> None:
        with mock.patch.object(publish_release, "checkTools"), \
             mock.patch.object(publish_release, "checkRepositoryState"), \
             mock.patch.object(publish_release, "checkVersionIsNew"), \
             mock.patch.object(publish_release, "writeVersion") as write_version, \
             mock.patch.object(publish_release, "commitAndTag") as commit_and_tag, \
             mock.patch.object(publish_release, "push") as push, \
             mock.patch.object(publish_release, "runTests") as run_tests:
            output = io.StringIO()
            with redirect_stdout(output):
                code = publish_release.main(["--date", "2026-09-25", "--beta", "2", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("2026.9.25-beta.2", output.getvalue())
        for step in (write_version, commit_and_tag, push, run_tests):
            step.assert_not_called()

    def test_declining_the_push_stops_before_pushing(self) -> None:
        with mock.patch.object(publish_release, "checkTools"), \
             mock.patch.object(publish_release, "checkRepositoryState"), \
             mock.patch.object(publish_release, "checkVersionIsNew"), \
             mock.patch.object(publish_release, "writeVersion"), \
             mock.patch.object(publish_release, "commitAndTag") as commit_and_tag, \
             mock.patch.object(publish_release, "push") as push, \
             mock.patch.object(publish_release, "confirm", return_value=False):
            with redirect_stdout(io.StringIO()):
                code = publish_release.main(["--date", "2026-09-25", "--skip-tests"])
        self.assertEqual(code, 1)
        commit_and_tag.assert_called_once_with("2026.9.25", [])
        push.assert_not_called()

    def test_stops_when_a_check_fails(self) -> None:
        error = publish_release.ReleaseError("コミットしていない変更があります")
        with mock.patch.object(publish_release, "checkTools"), \
             mock.patch.object(publish_release, "checkRepositoryState", side_effect=error), \
             mock.patch.object(publish_release, "writeVersion") as write_version:
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = publish_release.main([])
        self.assertEqual(code, 1)
        self.assertIn("コミットしていない変更", stderr.getvalue())
        write_version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
