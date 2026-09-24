pub mod app_paths;
pub mod reinstall;
pub mod uninstall;
pub mod updater;

/// テストの共通部品。
#[cfg(test)]
pub(crate) mod test_support {
    use std::sync::{Mutex, MutexGuard};

    static PROCESS_STATE: Mutex<()> = Mutex::new(());

    /// 環境変数や作業フォルダ (プロセス全体で 1 つ) を変えるテストは、これを持っている間だけ変える。
    /// cargo test はテストを並列に走らせるので、持たずに変えると別のテストの途中で値が変わる。
    pub(crate) fn lock_process_state() -> MutexGuard<'static, ()> {
        // 別のテストが失敗して毒が回っても、ロックとしては使い続ける。
        PROCESS_STATE.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

use std::fs::{create_dir_all, OpenOptions};
use std::io::{Error, Write};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{Emitter, Manager};

pub(crate) fn startup_log(message: &str) {
    let Ok(executable_path) = std::env::current_exe() else {
        return;
    };
    let log_path = app_paths::startup_log_path(&executable_path);
    let Some(log_directory) = log_path.parent() else {
        return;
    };
    if create_dir_all(log_directory).is_err() {
        return;
    }
    let Ok(mut log_file) = OpenOptions::new().create(true).append(true).open(log_path) else {
        return;
    };
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_secs());
    let _ = writeln!(log_file, "[{timestamp}] {message}");
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let data_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| app_paths::prepare_data_dir(&exe));
    startup_log("VRCT-0 startup began");
    if let Some(dir) = data_dir.as_ref().filter(|dir| !dir.is_dir()) {
        // ここに書けないならこのログも残らないが、書ける場合 (途中で作れた等) のために試す。
        startup_log(&format!("Could not create the data folder {}", dir.display()));
    }
    let updater = updater::Updater::new(Arc::new(updater::VelopackBackend::new(updater::REPO_URL)));
    let exit_updater = updater.clone();
    let result = tauri::Builder::default()
        .manage(updater)
        .setup(move |app| {
            let window_config = app
                .config()
                .app
                .windows
                .iter()
                .find(|window| window.label == "main")
                .cloned()
                .ok_or_else(|| Error::other("main window config is missing"))?;
            let mut builder = tauri::WebviewWindowBuilder::from_config(app.handle(), &window_config)?;
            if let Some(dir) = &data_dir {
                builder = builder.data_directory(dir.join("webview"));
            }
            let main_window = builder.build()?;
            main_window.show()?;
            if let Err(error) = main_window.set_focus() {
                startup_log(&format!("Main window focus failed: {error}"));
            }
            startup_log("Main window is ready");

            #[cfg(debug_assertions)]
            { main_window.open_devtools(); }

            let handle = app.handle().clone();
            app.state::<updater::Updater>().set_emitter(Arc::new(move |state| {
                let _ = handle.emit(updater::STATE_EVENT, state);
            }));

            Ok(())
        })
        // tauri-plugin-http (起動のたびに %LOCALAPPDATA%\com.lighfu.vrct0\.cookies を書く) と
        // tauri-plugin-fs は画面から使っていないので入れない。導入先の外に書かないため。
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_font_list,
            updater::updater_state,
            updater::updater_check,
            updater::updater_download,
            updater::updater_restart_now
        ])
        .build(tauri::generate_context!());
    match result {
        Ok(app) => app.run(move |_handle, event| {
            if let tauri::RunEvent::Exit = event {
                exit_updater.apply_on_exit();
                startup_log("VRCT-0 event loop ended");
            }
        }),
        Err(error) => {
            startup_log(&format!("VRCT-0 startup failed: {error}"));
            panic!("error while running tauri application: {error}");
        }
    }
}


use font_kit::{source::SystemSource};
use std::collections::HashSet;

#[tauri::command]
async fn get_font_list() -> Vec<String> {
    let source = SystemSource::new();
    let mut font_families = HashSet::new();

    if let Ok(fonts) = source.all_fonts() {
        for font in fonts {
            if let Ok(info) = font.load() {
                font_families.insert(info.family_name().to_string());
            }
        }
    }

    font_families.into_iter().collect()
}
