import { invoke } from "@tauri-apps/api/core";
import { useStore_AppUpdate } from "@store";

// アプリ内の更新。状態は Rust の更新役 (src-tauri/src/updater.rs) が持ち、
// "app-update://state" イベントで送ってくる (AppUpdateController が受ける)。
export const useAppUpdate = () => {
    const { currentAppUpdate, updateAppUpdate } = useStore_AppUpdate();

    const syncAppUpdateState = async () => {
        updateAppUpdate(await invoke("updater_state"));
    };
    const checkAppUpdate = (channel, manual) => invoke("updater_check", { channel, manual });
    const downloadAppUpdate = () => invoke("updater_download");
    const restartToApplyUpdate = () => invoke("updater_restart_now");

    return {
        currentAppUpdate,
        updateAppUpdate,
        syncAppUpdateState,
        checkAppUpdate,
        downloadAppUpdate,
        restartToApplyUpdate,
    };
};
