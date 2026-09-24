//! 導入先とデータの置き場所。
//!
//! Velopack で入れた版は `%LocalAppData%\VRCT-0\current\VRCT-0.exe` で動き、
//! 導入先の直下に `Update.exe` がある。そのときだけ `<導入先>\data` を
//! データの置き場所にする。開発中 (target\debug など) は今までどおり
//! 実行ファイルのフォルダを使う。

use std::path::{Path, PathBuf};

/// サイドカー (Python) にデータの置き場所を渡す環境変数。
pub const DATA_DIR_ENV: &str = "VRCT_DATA_DIR";

/// huggingface_hub と hf_xet のキャッシュの置き場所。既定は `%USERPROFILE%\.cache\huggingface` で、
/// hf_xet が使われると `hf_hub_download(cache_dir=...)` を渡してもチャンクキャッシュ (`xet\`) はそこに書かれる
/// (開発版で確認。配る版のサイドカーは hf_xet の配布情報 (dist-info) を含まず、huggingface_hub が hf_xet を
/// 使えないと判断して普通の HTTP で取るので、2026-09-24 の時点では書かれない)。同梱のしかたが変わっても
/// 導入先の外に書かないよう、入れた版では `data\huggingface` に向ける。
pub const HF_HOME_ENV: &str = "HF_HOME";

/// NVIDIA のドライバーが CUDA の JIT のキャッシュを置く場所。既定は `%APPDATA%\NVIDIA\ComputeCache` で、
/// サイドカーが CUDA の装置を数えるだけでもドライバーがこのフォルダを作る (2026-09-24 に空のプロフィールで確認)。
/// 入れた版では `data\nvidia\ComputeCache` に向ける。
pub const CUDA_CACHE_ENV: &str = "CUDA_CACHE_PATH";

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
///
/// `data\` を作れなくても環境変数は渡す。渡さないとサイドカーは開発版と同じく
/// `current\` に設定やモデルを書き、次の更新で消えてしまう。作れたかどうかは
/// 呼んだ側が `is_dir()` で確かめてログに残す。
pub fn prepare_data_dir(exe: &Path) -> Option<PathBuf> {
    let data_dir = data_dir_for(exe)?;
    let created = std::fs::create_dir_all(&data_dir).is_ok();
    std::env::set_var(DATA_DIR_ENV, &data_dir);
    std::env::set_var(HF_HOME_ENV, data_dir.join("huggingface"));
    std::env::set_var(CUDA_CACHE_ENV, data_dir.join("nvidia").join("ComputeCache"));
    if created {
        // 作業フォルダを変えられなくても、環境変数だけで設定とモデルは data\ に入る。
        let _ = std::env::set_current_dir(&data_dir);
    }
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

    /// 環境変数と作業フォルダを変えたテストのあとで元に戻す。
    struct ProcessStateGuard {
        dir: PathBuf,
        env: Vec<(&'static str, Option<std::ffi::OsString>)>,
    }

    impl ProcessStateGuard {
        fn save() -> Self {
            ProcessStateGuard {
                dir: std::env::current_dir().unwrap(),
                env: [DATA_DIR_ENV, HF_HOME_ENV, CUDA_CACHE_ENV]
                    .into_iter()
                    .map(|name| (name, std::env::var_os(name)))
                    .collect(),
            }
        }
    }

    impl Drop for ProcessStateGuard {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.dir);
            for (name, value) in &self.env {
                match value {
                    Some(value) => std::env::set_var(name, value),
                    None => std::env::remove_var(name),
                }
            }
        }
    }

    #[test]
    fn prepare_sets_env_and_working_dir() {
        let _lock = crate::test_support::lock_process_state();
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        let _restore = ProcessStateGuard::save();
        let data = prepare_data_dir(&exe).expect("installed layout");
        let env_value = std::env::var_os(DATA_DIR_ENV);
        let working_dir = std::env::current_dir().unwrap();

        assert_eq!(data, root.join("data"));
        assert!(data.is_dir());
        assert_eq!(env_value, Some(data.clone().into_os_string()));
        assert_eq!(fs::canonicalize(working_dir).unwrap(), fs::canonicalize(&data).unwrap());
    }

    #[test]
    fn prepare_points_the_hugging_face_cache_into_data() {
        let _lock = crate::test_support::lock_process_state();
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        let _restore = ProcessStateGuard::save();
        std::env::set_var(HF_HOME_ENV, r"C:\somewhere\else");
        prepare_data_dir(&exe).expect("installed layout");

        // 利用者が自分で HF_HOME を設定していても、導入先の外には書かせない。
        assert_eq!(
            std::env::var_os(HF_HOME_ENV),
            Some(root.join("data").join("huggingface").into_os_string())
        );
    }

    #[test]
    fn prepare_points_the_nvidia_compute_cache_into_data() {
        let _lock = crate::test_support::lock_process_state();
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        let _restore = ProcessStateGuard::save();
        prepare_data_dir(&exe).expect("installed layout");

        assert_eq!(
            std::env::var_os(CUDA_CACHE_ENV),
            Some(root.join("data").join("nvidia").join("ComputeCache").into_os_string())
        );
    }

    #[test]
    fn prepare_still_passes_the_data_dir_when_it_cannot_be_created() {
        let _lock = crate::test_support::lock_process_state();
        let (_tmp, root, exe) = installed_layout("VRCT-0", "current");
        // data という名前のファイルがあるとフォルダを作れない。
        fs::write(root.join("data"), b"not a folder").unwrap();
        let _restore = ProcessStateGuard::save();
        let before = std::env::current_dir().unwrap();
        let data = prepare_data_dir(&exe);
        let env_value = std::env::var_os(DATA_DIR_ENV);
        let working_dir = std::env::current_dir().unwrap();

        assert_eq!(data, Some(root.join("data")));
        assert!(!root.join("data").is_dir());
        // サイドカーが current\ に書かないよう、環境変数は渡す。作業フォルダは変えない。
        assert_eq!(env_value, Some(root.join("data").into_os_string()));
        assert_eq!(working_dir, before);
    }
}
