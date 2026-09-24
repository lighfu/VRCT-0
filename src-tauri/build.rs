fn main() {
    // tauri-build は exe に埋め込むアイコン (icons/icon.ico) の変更を追わないので、
    // アイコンを差し替えたらビルドスクリプトを走らせ直す (target を残したビルドで古いアイコンが残らないように)
    println!("cargo:rerun-if-changed=icons/icon.ico");
    tauri_build::build()
}
