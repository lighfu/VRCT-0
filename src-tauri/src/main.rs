// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Velopack の導入・更新・削除のフックを処理する (フックのときはここで終了する)。
    // 入れてある上から Setup を実行したときは、Setup が退避したフォルダから data\ を戻す。
    // 削除の直前には、動いている本体とサイドカーを止めてファイルを消せるようにする。
    velopack::VelopackApp::build()
        .on_after_install_fast_callback(|_version| vrct_lib::reinstall::restore_after_install())
        .on_before_uninstall_fast_callback(|_version| vrct_lib::uninstall::before_uninstall())
        .run();
    vrct_lib::run()
}
