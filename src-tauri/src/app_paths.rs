//! 導入先とデータの置き場所。
//!
//! Velopack で入れた版は `%LocalAppData%\VRCT-0\current\VRCT-0.exe` で動き、
//! 導入先の直下に `Update.exe` がある。そのときだけ `<導入先>\data` を
//! データの置き場所にする。開発中 (target\debug など) は今までどおり
//! 実行ファイルのフォルダを使う。

use std::path::{Path, PathBuf};

/// サイドカー (Python) にデータの置き場所を渡す環境変数。
pub const DATA_DIR_ENV: &str = "VRCT_DATA_DIR";

/// Velopack で入れた版なら導入先 (`current\` の親) を返す。
pub fn install_root(exe: &Path) -> Option<PathBuf> {
    let bin_dir = exe.parent()?;
    let is_current = bin_dir
        .file_name()
        .map(|name| name.to_string_lossy().eq_ignore_ascii_case("current"))
        .unwrap_or(false);
    if !is_current {
        return None;
    }
    let root = bin_dir.parent()?;
    root.join("Update.exe").is_file().then(|| root.to_path_buf())
}

/// Velopack で入れた版ならデータの置き場所 (`<導入先>\data`) を返す。
pub fn data_dir_for(exe: &Path) -> Option<PathBuf> {
    install_root(exe).map(|root| root.join("data"))
}

/// 起動の最初に呼ぶ。入れた版なら `data\` を作り、環境変数と作業フォルダを
/// そこへ向ける。どちらもサイドカーに引き継がれるので、サイドカーが相対パスで
/// 書くログ (process.log など) も、更新で消える `current\` ではなく `data\` に入る。
pub fn prepare_data_dir(exe: &Path) -> Option<PathBuf> {
    let data_dir = data_dir_for(exe)?;
    std::fs::create_dir_all(&data_dir).ok()?;
    std::env::set_var(DATA_DIR_ENV, &data_dir);
    // 作業フォルダを変えられなくても、環境変数だけで設定とモデルは data\ に入る。
    let _ = std::env::set_current_dir(&data_dir);
    Some(data_dir)
}

/// 起動ログの場所。入れた版は `data\logs\startup.log`、それ以外は実行ファイルの隣の `logs\`。
pub fn startup_log_path(exe: &Path) -> PathBuf {
    let base = data_dir_for(exe)
        .unwrap_or_else(|| exe.parent().unwrap_or(Path::new(".")).to_path_buf());
    base.join("logs").join("startup.log")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// `<tmp>\<sub>\current\VRCT-0.exe` と `<tmp>\<sub>\Update.exe` を作る。
    fn installed_layout(sub: &str, current: &str) -> (tempfile::TempDir, PathBuf, PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join(sub);
        fs::create_dir_all(root.join(current)).unwrap();
        fs::write(root.join("Update.exe"), b"").unwrap();
        let exe = root.join(current).join("VRCT-0.exe");
        (tmp, root, exe)
    }

    #[test]
    fn install_root_is_found_for_a_velopack_layout() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        assert_eq!(install_root(&exe), Some(root));
    }

    #[test]
    fn install_root_accepts_any_case_of_current() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "Current");
        assert_eq!(install_root(&exe), Some(root));
    }

    #[test]
    fn install_root_works_with_quotes_and_japanese_in_the_path() {
        let (_tmp, root, exe) = installed_layout("さくら's VRCT-0", "current");
        assert_eq!(install_root(&exe), Some(root.clone()));
        assert_eq!(data_dir_for(&exe), Some(root.join("data")));
    }

    #[test]
    fn dev_build_is_not_installed() {
        let tmp = tempfile::tempdir().unwrap();
        let exe = tmp.path().join("target").join("debug").join("VRCT-0.exe");
        assert_eq!(install_root(&exe), None);
        assert_eq!(data_dir_for(&exe), None);
    }

    #[test]
    fn current_folder_without_update_exe_is_not_installed() {
        let tmp = tempfile::tempdir().unwrap();
        fs::create_dir_all(tmp.path().join("current")).unwrap();
        let exe = tmp.path().join("current").join("VRCT-0.exe");
        assert_eq!(install_root(&exe), None);
    }

    #[test]
    fn startup_log_goes_to_data_when_installed() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        assert_eq!(startup_log_path(&exe), root.join("data").join("logs").join("startup.log"));
    }

    #[test]
    fn startup_log_stays_next_to_the_exe_in_dev() {
        assert_eq!(
            startup_log_path(Path::new(r"C:\VRCT\VRCT-0.exe")),
            Path::new(r"C:\VRCT\logs\startup.log")
        );
    }

    #[test]
    fn prepare_does_nothing_in_dev() {
        let tmp = tempfile::tempdir().unwrap();
        let exe = tmp.path().join("VRCT-0.exe");
        assert_eq!(prepare_data_dir(&exe), None);
        assert!(!tmp.path().join("data").exists());
    }

    #[test]
    fn prepare_sets_env_and_working_dir() {
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        let saved_dir = std::env::current_dir().unwrap();
        let data = prepare_data_dir(&exe).expect("installed layout");
        let env_value = std::env::var_os(DATA_DIR_ENV);
        let working_dir = std::env::current_dir().unwrap();
        std::env::set_current_dir(&saved_dir).unwrap();
        std::env::remove_var(DATA_DIR_ENV);

        assert_eq!(data, root.join("data"));
        assert!(data.is_dir());
        assert_eq!(env_value, Some(data.clone().into_os_string()));
        assert_eq!(fs::canonicalize(working_dir).unwrap(), fs::canonicalize(&data).unwrap());
    }
}
