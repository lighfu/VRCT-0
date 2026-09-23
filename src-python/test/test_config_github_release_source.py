"""配布元切り替え(Hugging Face -> このフォークのGitHub Releases)に関する
config.py の設定値テスト。"""

import unittest

from config import config


class TestConfigGithubReleaseSource(unittest.TestCase):
    def test_repo_is_this_fork(self) -> None:
        self.assertEqual(config.SOFTWARE_RELEASE_GITHUB_REPO, "lighfu/VRCT-0")

    def test_github_url_points_at_this_fork(self) -> None:
        self.assertEqual(
            config.GITHUB_URL,
            "https://api.github.com/repos/lighfu/VRCT-0/releases/latest",
        )

    def test_github_releases_list_url_points_at_this_fork(self) -> None:
        self.assertEqual(
            config.GITHUB_RELEASES_LIST_URL,
            "https://api.github.com/repos/lighfu/VRCT-0/releases",
        )

    def test_setup_download_url_uses_version_tag_on_this_fork(self) -> None:
        self.assertEqual(
            config.setupDownloadUrlForVersion("3.5.1-beta.1"),
            "https://github.com/lighfu/VRCT-0/releases/download/v3.5.1-beta.1/VRCT_setup.exe",
        )

    def test_setup_download_url_for_tag_uses_the_tag_verbatim(self) -> None:
        # Must use the given tag exactly as-is (not "v" + something derived
        # from it), so a release whose tag_name diverges from "v<name>"
        # still resolves to the correct asset.
        self.assertEqual(
            config.setupDownloadUrlForTag("v3.5.1-beta.1+build.7"),
            "https://github.com/lighfu/VRCT-0/releases/download/v3.5.1-beta.1+build.7/VRCT_setup.exe",
        )


if __name__ == "__main__":
    unittest.main()
