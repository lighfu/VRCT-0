import { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useStore_AppUpdate } from "@store";

// アプリ内の更新。状態は Rust の更新役 (src-tauri/src/updater.rs) が持ち、
// "app-update://state" イベントで送ってくる (useAppUpdateStateListener が受ける)。
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

// Rust の更新役から状態を受け取り続ける。表示されている間だけ受け取るので、
// 普段は AppUpdateController が、エラー画面ではその更新ボタンが使う
// (エラー画面では AppUpdateController ごと外れるため)。
export const useAppUpdateStateListener = () => {
    const { updateAppUpdate, syncAppUpdateState } = useAppUpdate();

    useEffect(() => {
        let unlisten = null;
        let is_unmounted = false;
        listen("app-update://state", (event) => updateAppUpdate(event.payload)).then((fn) => {
            if (is_unmounted) fn();
            else unlisten = fn;
        });
        syncAppUpdateState().catch((error) => console.error("[AppUpdate]", error));
        return () => {
            is_unmounted = true;
            if (unlisten) unlisten();
        };
    }, []);
};
