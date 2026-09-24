// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Velopack の導入・更新・削除のフックを処理する (フックのときはここで終了する)。
    // 削除の直前には、動いている本体とサイドカーを止めてファイルを消せるようにする。
    velopack::VelopackApp::build()
        .on_before_uninstall_fast_callback(|_version| vrct_lib::uninstall::stop_app_processes())
        .run();
    vrct_lib::run()
}
