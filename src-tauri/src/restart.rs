//! 画面からの再起動 (GPU 高速化パックの導入・削除を反映させるため)。
//!
//! 画面はサイドカーに /run/shutdown を送って 2 秒待ってから app_restart を呼ぶ。
//! 準備済みの更新があれば、更新役の「今すぐ再起動」と同じ道で終わる
//! (Velopack が入れ替えてから起動し直す)。入れ替え中に新しいアプリを起動すると
//! Velopack に止められるので、ここで普通に起動し直してはいけない。
//!
//! どちらの道も RunEvent::Exit を通す。tauri-plugin-shell はそこでだけサイドカーを止めるので、
//! 通らずに起動し直すと前のサイドカーが残る (RAM と、GPU を使っていれば VRAM と GPU 高速化パックの
//! DLL を掴んだまま。DLL が消せないので削除と導入し直しも失敗する)。

use crate::updater::{UpdateState, Updater};

#[derive(Debug, PartialEq)]
pub enum RestartPath {
    ViaUpdater,
    Plain,
}

pub fn restart_path(state: &UpdateState) -> RestartPath {
    match state {
        UpdateState::Ready { .. } => RestartPath::ViaUpdater,
        UpdateState::NotInstalled
        | UpdateState::Idle
        | UpdateState::Checking
        | UpdateState::UpToDate
        | UpdateState::Available { .. }
        | UpdateState::Downloading { .. }
        | UpdateState::Failed { .. } => RestartPath::Plain,
    }
}

#[tauri::command]
pub fn app_restart(app: tauri::AppHandle, updater: tauri::State<'_, Updater>) {
    crate::startup_log("Restart requested from the UI");
    if restart_path(&updater.state()) == RestartPath::ViaUpdater && updater.request_restart() {
        // RunEvent::Exit で更新役が入れ替えを頼み、Velopack が起動し直す。
        app.exit(0);
    } else {
        // app.restart() は使わない。このコマンドは async でないのでメインスレッドで動き、
        // Tauri 2.5.1 の AppHandle::restart() はメインスレッドから呼ばれると RunEvent::Exit を
        // 出さずに起動し直す (tauri-2.5.1/src/app.rs 542〜566 行)。request_restart() は
        // ExitRequested → Exit (shell プラグインがサイドカーを止め、lib.rs の Exit の処理が動く) を
        // 通してから起動し直す (app.rs 568〜578 行、1285〜1291 行)。
        // 確かめたあとで更新の準備ができても、この終わり方では入れ替えない。
        updater.skip_apply_on_exit();
        app.request_restart();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::updater::FailedStage;

    #[test]
    fn ready_update_restarts_through_the_updater() {
        let state = UpdateState::Ready { version: "3.6.0".into(), restart_requested: false };
        assert_eq!(restart_path(&state), RestartPath::ViaUpdater);
    }

    #[test]
    fn plain_path_goes_through_run_event_exit() {
        // AppHandle::restart() はこのコマンドから呼ぶと RunEvent::Exit を出さず、前のサイドカーが残る。
        let source = include_str!("restart.rs");
        let code: Vec<&str> = source
            .split("#[cfg(test)]")
            .next()
            .unwrap()
            .lines()
            .filter(|line| !line.trim_start().starts_with("//"))
            .collect();
        let code = code.join("\n");
        assert!(code.contains("app.request_restart()"));
        assert!(!code.contains("app.restart()"));
    }

    #[test]
    fn everything_else_restarts_plainly() {
        let states = [
            UpdateState::NotInstalled,
            UpdateState::Idle,
            UpdateState::Checking,
            UpdateState::UpToDate,
            UpdateState::Available { version: "3.6.0".into(), size_bytes: 1, is_downgrade: false },
            UpdateState::Downloading { version: "3.6.0".into(), percent: 5 },
            UpdateState::Failed { stage: FailedStage::Download, message: "x".into(), version: None },
        ];
        for state in states {
            assert_eq!(restart_path(&state), RestartPath::Plain, "{state:?}");
        }
    }
}
