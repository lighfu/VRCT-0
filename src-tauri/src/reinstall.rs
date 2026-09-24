//! 入れてある上から `VRCT-0-win-Setup.exe` を実行したとき (修復・更新・ダウングレード) に `data\` を残す。
//!
//! Velopack 1.2.158 の Setup は、導入先がすでにあると、それを丸ごと `<導入先>.<英数字 16 文字>`
//! (例 `%LocalAppData%\VRCT-0.ScVeYIavcRWjeoKw`) へ名前を変えて退避し (失敗したときに戻すため)、
//! 空にした導入先へ入れ直す。入れ終えると退避したフォルダを消すので、何もしないと中の `data\`
//! (設定・API キー・単語の一覧・モデル) も一緒に消える (2026-09-24 に実機で確認)。
//!
//! 入れ直しの途中、退避したフォルダを消す前に、導入のフック (`--veloapp-install`) が新しい
//! `current\VRCT-0.exe` で呼ばれる (Setup の `install.rs` の順序。実機のログでも確認)。
//! そこで退避したフォルダの `data\` を導入先へ移す。同じドライブの中の名前の変更なので、
//! モデルが何 GB あっても一瞬で終わり、Velopack のフックの制限 (30 秒) に収まる。
//!
//! 残る穴: フックのあとで Setup が失敗すると (アンインストールの登録やアプリの起動の失敗)、
//! Setup は新しい導入先を消して退避したフォルダを戻すので、移した `data\` も消える。

use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use crate::app_paths::install_root;

/// Setup が退避に使う名前の、導入先の名前のあとに付く英数字の数 (`shared::random_string(16)`)。
const ROLLBACK_SUFFIX_LEN: usize = 16;

/// Setup が退避したフォルダか。`Path::with_extension(<英数字 16 文字>)` で付けた名前と照らす。
fn is_rollback_name(root: &Path, candidate: &Path) -> bool {
    let (Some(stem), Some(name)) = (root.file_stem(), candidate.file_name()) else {
        return false;
    };
    let (stem, name) = (stem.to_string_lossy(), name.to_string_lossy());
    let Some(suffix) = name.strip_prefix(stem.as_ref()).and_then(|rest| rest.strip_prefix('.')) else {
        return false;
    };
    suffix.len() == ROLLBACK_SUFFIX_LEN && suffix.chars().all(|c| c.is_ascii_alphanumeric())
}

/// 導入先の隣にある、Setup が退避したフォルダ (中身は問わない)。
pub fn rollback_dirs(root: &Path) -> Vec<PathBuf> {
    let Some(parent) = root.parent() else {
        return Vec::new();
    };
    let Ok(entries) = std::fs::read_dir(parent) else {
        return Vec::new();
    };
    entries
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| path.is_dir() && is_rollback_name(root, path))
        .collect()
}

fn modified(path: &Path) -> SystemTime {
    std::fs::metadata(path).and_then(|m| m.modified()).unwrap_or(SystemTime::UNIX_EPOCH)
}

/// 退避したフォルダの `data\` を導入先へ戻す。戻したら、戻した元のフォルダを返す。
/// 退避したフォルダが複数あるとき (前の Setup が消し損ねた等) は、`data\` がいちばん新しいものを使う。
pub fn restore_data(root: &Path) -> Result<Option<PathBuf>, String> {
    let Some(source) = rollback_dirs(root)
        .into_iter()
        .filter(|dir| dir.join("data").is_dir())
        .max_by_key(|dir| modified(&dir.join("data")))
    else {
        return Ok(None);
    };
    let from = source.join("data");
    let to = root.join("data");
    if to.exists() {
        // 空のフォルダなら置き換える。中身があるなら、どちらかを消すことになるので触らない。
        let is_empty = std::fs::read_dir(&to).map(|mut entries| entries.next().is_none()).unwrap_or(false);
        if !is_empty {
            return Err(format!("{} already has files; left {} as it is", to.display(), from.display()));
        }
        std::fs::remove_dir(&to).map_err(|e| format!("could not remove the empty {}: {e}", to.display()))?;
    }
    // ウイルス対策ソフトが一瞬ファイルを開いていることがあるので、少しのあいだ繰り返す。
    let mut last_error = None;
    for _ in 0..20 {
        match std::fs::rename(&from, &to) {
            Ok(()) => return Ok(Some(source)),
            Err(error) => {
                last_error = Some(error);
                std::thread::sleep(Duration::from_millis(250));
            }
        }
    }
    Err(format!(
        "could not move {} to {}: {}",
        from.display(),
        to.display(),
        last_error.map(|e| e.to_string()).unwrap_or_default()
    ))
}

/// 導入のフック (`--veloapp-install`) から呼ぶ。
pub fn restore_after_install() {
    let Ok(exe) = std::env::current_exe() else { return };
    let Some(root) = install_root(&exe) else { return };
    // 起動ログは data\ の中に書くので、戻したあとに書く (先に書くと空の data\ ができる)。
    match restore_data(&root) {
        Ok(Some(source)) => crate::startup_log(&format!("Reinstall: kept the data folder from {}", source.display())),
        Ok(None) => {}
        Err(message) => crate::startup_log(&format!("Reinstall: could not keep the data folder: {message}")),
    }
}

/// 削除の直前のフックから呼ぶ。Setup が消し損ねた退避フォルダ (古い data\ が入っていることがある) も消す。
pub fn remove_rollback_dirs(root: &Path) {
    for dir in rollback_dirs(root) {
        let _ = std::fs::remove_dir_all(dir);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn root_in(tmp: &tempfile::TempDir) -> PathBuf {
        let root = tmp.path().join("VRCT-0");
        fs::create_dir_all(root.join("current")).unwrap();
        root
    }

    /// Setup が退避したフォルダを真似る。
    fn rollback_with_data(tmp: &tempfile::TempDir, suffix: &str, config: &str) -> PathBuf {
        let dir = tmp.path().join(format!("VRCT-0.{suffix}"));
        fs::create_dir_all(dir.join("data").join("weights")).unwrap();
        fs::create_dir_all(dir.join("current")).unwrap();
        fs::write(dir.join("data").join("config.json"), config).unwrap();
        fs::write(dir.join("data").join("weights").join("model.bin"), b"weights").unwrap();
        dir
    }

    #[test]
    fn rollback_names_follow_setup() {
        let root = Path::new(r"C:\Users\u\AppData\Local\VRCT-0");
        let parent = root.parent().unwrap();
        assert!(is_rollback_name(root, &parent.join("VRCT-0.ScVeYIavcRWjeoKw")));
        assert!(!is_rollback_name(root, &parent.join("VRCT-0.ScVeYIavcRWjeoK")));
        assert!(!is_rollback_name(root, &parent.join("VRCT-0.ScVeYIavcRWjeoKw1")));
        assert!(!is_rollback_name(root, &parent.join("VRCT-0.ScVeYIavc-WjeoKw")));
        assert!(!is_rollback_name(root, &parent.join("VRCT-0")));
        assert!(!is_rollback_name(root, &parent.join("VRCT-0-data")));
        assert!(!is_rollback_name(root, &parent.join("VRCT.ScVeYIavcRWjeoKw")));
    }

    #[test]
    fn nothing_to_restore_on_a_fresh_install() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        fs::create_dir_all(tmp.path().join("VRCT-0.old")).unwrap();
        assert_eq!(restore_data(&root), Ok(None));
        assert!(!root.join("data").exists());
    }

    #[test]
    fn data_is_moved_back_from_the_rollback_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        let rollback = rollback_with_data(&tmp, "ScVeYIavcRWjeoKw", r#"{"UI_LANGUAGE": "ja"}"#);

        assert_eq!(restore_data(&root), Ok(Some(rollback.clone())));
        assert_eq!(fs::read_to_string(root.join("data").join("config.json")).unwrap(), r#"{"UI_LANGUAGE": "ja"}"#);
        assert!(root.join("data").join("weights").join("model.bin").is_file());
        // Setup があとで消すのは、data\ を抜いた残り。
        assert!(!rollback.join("data").exists());
        assert!(rollback.join("current").is_dir());
    }

    #[test]
    fn rollback_folder_without_data_is_ignored() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        fs::create_dir_all(tmp.path().join("VRCT-0.ScVeYIavcRWjeoKw").join("current")).unwrap();
        assert_eq!(restore_data(&root), Ok(None));
    }

    #[test]
    fn existing_data_with_files_is_not_overwritten() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        let rollback = rollback_with_data(&tmp, "ScVeYIavcRWjeoKw", "old");
        fs::create_dir_all(root.join("data")).unwrap();
        fs::write(root.join("data").join("config.json"), "new").unwrap();

        assert!(restore_data(&root).is_err());
        assert_eq!(fs::read_to_string(root.join("data").join("config.json")).unwrap(), "new");
        assert_eq!(fs::read_to_string(rollback.join("data").join("config.json")).unwrap(), "old");
    }

    #[test]
    fn empty_data_folder_is_replaced() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        rollback_with_data(&tmp, "ScVeYIavcRWjeoKw", "kept");
        fs::create_dir_all(root.join("data")).unwrap();

        assert!(restore_data(&root).unwrap().is_some());
        assert_eq!(fs::read_to_string(root.join("data").join("config.json")).unwrap(), "kept");
    }

    #[cfg(windows)]
    #[test]
    fn the_newest_rollback_folder_wins() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        let old = rollback_with_data(&tmp, "AAAAAAAAAAAAAAAA", "stale");
        let new = rollback_with_data(&tmp, "BBBBBBBBBBBBBBBB", "latest");
        set_modified(&old.join("data"), SystemTime::now() - Duration::from_secs(3600));
        set_modified(&new.join("data"), SystemTime::now());

        assert_eq!(restore_data(&root), Ok(Some(new)));
        assert_eq!(fs::read_to_string(root.join("data").join("config.json")).unwrap(), "latest");
        assert!(old.join("data").is_dir());
    }

    #[test]
    fn uninstall_removes_leftover_rollback_folders_only() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(&tmp);
        let leftover = rollback_with_data(&tmp, "ScVeYIavcRWjeoKw", "old");
        let unrelated = tmp.path().join("VRCT-0.backup");
        fs::create_dir_all(&unrelated).unwrap();

        remove_rollback_dirs(&root);
        assert!(!leftover.exists());
        assert!(unrelated.is_dir());
        assert!(root.is_dir());
    }

    /// フォルダの更新時刻を変える (Windows ではフォルダを開くのに FILE_FLAG_BACKUP_SEMANTICS が要る)。
    #[cfg(windows)]
    fn set_modified(dir: &Path, time: SystemTime) {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_WRITE_ATTRIBUTES: u32 = 0x0100;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
        let file = fs::OpenOptions::new()
            .access_mode(FILE_WRITE_ATTRIBUTES)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
            .open(dir)
            .unwrap();
        file.set_modified(time).unwrap();
    }
}
