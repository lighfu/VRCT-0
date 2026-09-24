import { useEffect, useRef } from "react";
import { useAppUpdate, useAppUpdateStateListener, useIsBackendReady } from "@logics_common";
import { useUpdater } from "@logics_configs";

// Rust の更新役から状態を受け取り、起動して設定を読み終えたら 1 回だけ新しい版を確かめる。
export const AppUpdateController = () => {
    const { checkAppUpdate } = useAppUpdate();
    const { currentIsBackendReady } = useIsBackendReady();
    const { currentReleaseChannel } = useUpdater();
    const is_checked = useRef(false);

    useAppUpdateStateListener();

    useEffect(() => {
        if (is_checked.current) return;
        if (currentIsBackendReady.data !== true) return;
        if (currentReleaseChannel.state !== "ok") return;
        is_checked.current = true;
        checkAppUpdate(currentReleaseChannel.data, false).catch((error) => console.error("[AppUpdate]", error));
    }, [currentIsBackendReady.data, currentReleaseChannel.state]);

    return null;
};
