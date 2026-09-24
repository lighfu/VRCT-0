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

/// PowerShell の単一引用符の文字列の中身。PowerShell は `'` のほかに U+2018〜U+201B
/// (‘ ’ ‚ ‛) も単一引用符として扱うので、どれも 2 つ重ねて文字そのものにする
/// (例: `O’Brien` という名前のユーザーフォルダ)。
pub fn ps_single_quoted_body(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        out.push(c);
        if matches!(c, '\'' | '\u{2018}' | '\u{2019}' | '\u{201A}' | '\u{201B}') {
            out.push(c);
        }
    }
    out
}

/// PowerShell に渡すスクリプト。`current_dir` の下から動いているプロセスを止める。
/// パスは単一引用符で囲み、中の引用符は 2 つ重ねる。
pub fn stop_script(current_dir: &Path, self_pid: u32) -> String {
    let mut prefix = current_dir.to_string_lossy().to_string();
    if !prefix.ends_with('\\') {
        prefix.push('\\');
    }
    let quoted = ps_single_quoted_body(&prefix);
    format!(
        "$prefix = '{quoted}'; \
         Get-CimInstance Win32_Process | \
         Where-Object {{ $_.ExecutablePath -and \
         $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and \
         $_.ProcessId -ne {self_pid} }} | \
         ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
}

/// 削除の直前のフックから呼ぶ。動いている VRCT-0 を止め、Setup が消し損ねた退避フォルダも消す。
pub fn before_uninstall() {
    let Ok(exe) = std::env::current_exe() else { return };
    let Some(root) = install_root(&exe) else { return };
    stop_app_processes(&root);
    crate::reinstall::remove_rollback_dirs(&root);
}

/// 導入先の `current\` から動いているプロセスを止める。最長 20 秒で打ち切る。
pub fn stop_app_processes(root: &Path) {
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
    fn stop_script_escapes_typographic_quotes() {
        let script = stop_script(Path::new("C:\\Users\\O\u{2019}Brien\\VRCT-0\\current"), 1);
        assert!(script.contains("'C:\\Users\\O\u{2019}\u{2019}Brien\\VRCT-0\\current\\'"));
    }

    /// PowerShell 自身に読ませて、引用符を含むパスがそのまま戻ることを確かめる。
    #[cfg(windows)]
    #[test]
    fn powershell_reads_the_quoted_path_back_unchanged() {
        let path = "C:\\Users\\O\u{2019}Brien \u{2018}x\u{201A}\u{201B}' さくら\\VRCT-0\\current\\";
        let expected: Vec<String> = path.encode_utf16().map(|unit| unit.to_string()).collect();
        // 文字コードの違いで化けないよう、UTF-16 の値を数字で出させる。
        let command = format!("([int[]][char[]]'{}') -join ','", ps_single_quoted_body(path));
        let output = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &command])
            .output()
            .expect("powershell.exe");
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), expected.join(","));
    }

    #[test]
    fn stop_script_does_not_double_the_trailing_backslash() {
        let script = stop_script(Path::new("C:\\x\\current\\"), 1);
        assert!(script.contains(r"'C:\x\current\'"));
        assert!(!script.contains(r"current\\'"));
    }
}
