//! 画面からの再起動 (GPU 部品の導入・削除を反映させるため)。
//!
//! 画面はサイドカーに /run/shutdown を送って 2 秒待ってから app_restart を呼ぶ。
//! 準備済みの更新があれば、更新役の「今すぐ再起動」と同じ道で終わる
//! (Velopack が入れ替えてから起動し直す)。入れ替え中に新しいアプリを起動すると
//! Velopack に止められるので、ここで普通に起動し直してはいけない。

use crate::updater::{UpdateState, Updater};

#[derive(Debug, PartialEq)]
pub enum RestartPath {
    ViaUpdater,
    Plain,
}

pub fn restart_path(state: &UpdateState) -> RestartPath {
    match state {
        UpdateState::Ready { .. } => RestartPath::ViaUpdater,
        _ => RestartPath::Plain,
    }
}

#[tauri::command]
pub fn app_restart(app: tauri::AppHandle, updater: tauri::State<'_, Updater>) {
    crate::startup_log("Restart requested from the UI");
    if restart_path(&updater.state()) == RestartPath::ViaUpdater && updater.request_restart() {
        // RunEvent::Exit で更新役が入れ替えを頼み、Velopack が起動し直す。
        app.exit(0);
    } else {
        app.restart();
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
