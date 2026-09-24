import { useEffect } from "react";
import { useHotkeys } from "@logics_configs";
import { useIsBackendReady, useIsVrctAvailable } from "@logics_common";

export const GlobalHotKeyController = () => {
    const { currentIsBackendReady } = useIsBackendReady();
    const { registerShortcuts, unregisterAll } = useHotkeys();
    const { currentIsVrctAvailable } = useIsVrctAvailable();

    useEffect(() => {
        if (currentIsVrctAvailable.data && currentIsBackendReady.data) {
            registerShortcuts();
        } else {
            unregisterAll();
        }
    }, [currentIsBackendReady.data, currentIsVrctAvailable.data]);

    return null;
};
