//! 削除 (アンインストール) の直前に、導入先の `current\` から動いている VRCT-0 を止める。
//!
//! Velopack は削除の直前に `VRCT-0.exe` をフック用の引数で別に起動し、30 秒以内に
//! 終わることを求める。本体やサイドカーが動いたままだとファイルが使用中で消せないので、
//! ここで止める。導入先の直下にある Update.exe (削除を進めている Velopack 自身) と、
//! このフックのプロセス自身は止めない。AI CLI の子プロセスは、サイドカーが終わると
//! 標準入力の終わりを受けて自分で終了する (2026-09-24 に実機で確認済み)。

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use crate::app_paths::install_root;

/// PowerShell に渡すスクリプト。`current_dir` の下から動いているプロセスを止める。
/// パスは単一引用符で囲み、中の `'` は `''` にする。
pub fn stop_script(current_dir: &Path, self_pid: u32) -> String {
    let mut prefix = current_dir.to_string_lossy().to_string();
    if !prefix.ends_with('\\') {
        prefix.push('\\');
    }
    let quoted = prefix.replace('\'', "''");
    format!(
        "$prefix = '{quoted}'; \
         Get-CimInstance Win32_Process | \
         Where-Object {{ $_.ExecutablePath -and \
         $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and \
         $_.ProcessId -ne {self_pid} }} | \
         ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
}

/// 導入先の `current\` から動いているプロセスを止める。最長 20 秒で打ち切る。
pub fn stop_app_processes() {
    let Ok(exe) = std::env::current_exe() else { return };
    let Some(root) = install_root(&exe) else { return };
    let script = stop_script(&root.join("current"), std::process::id());

    let mut command = Command::new("powershell.exe");
    command.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", &script]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let Ok(mut child) = command.spawn() else { return };

    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
        }
    }
    let _ = child.kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_script_targets_only_the_current_folder() {
        let script = stop_script(Path::new(r"C:\Users\u\AppData\Local\VRCT-0\current"), 42);
        assert!(script.contains(r"$prefix = 'C:\Users\u\AppData\Local\VRCT-0\current\'"));
        assert!(!script.contains("Update.exe"));
    }

    #[test]
    fn stop_script_skips_its_own_process() {
        let script = stop_script(Path::new(r"C:\x\current"), 4242);
        assert!(script.contains("$_.ProcessId -ne 4242"));
    }

    #[test]
    fn stop_script_escapes_single_quotes_and_keeps_japanese() {
        let script = stop_script(Path::new(r"C:\Users\さくら's\VRCT-0\current"), 1);
        assert!(script.contains(r"'C:\Users\さくら''s\VRCT-0\current\'"));
    }

    #[test]
    fn stop_script_does_not_double_the_trailing_backslash() {
        let script = stop_script(Path::new("C:\\x\\current\\"), 1);
        assert!(script.contains(r"'C:\x\current\'"));
        assert!(!script.contains(r"current\\'"));
    }
}
