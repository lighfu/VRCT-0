import clsx from "clsx";
import styles from "./ModalController.module.scss";
import { useStore_OpenedQuickSetting } from "@store";
import { Vr, Updater } from "@setting_box";
import { CudaPackPrompt } from "../cuda_pack_prompt/CudaPackPrompt";

export const ModalController = () => {
    const { currentOpenedQuickSetting, updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    if (currentOpenedQuickSetting.data === "") return null;
    return (
        <div className={styles.container}>
            <div className={styles.bg_onclick_close_area} onClick={() => updateOpenedQuickSetting("")}></div>
            <div className={clsx(styles.wrapper, currentOpenedQuickSetting.data === "cuda_pack_prompt" && styles.wrapper_compact)}>
                <QuickSettingsController />
            </div>
        </div>
    );
};

const QuickSettingsController = () => {
    const { currentOpenedQuickSetting, updateOpenedQuickSetting } = useStore_OpenedQuickSetting();

    switch (currentOpenedQuickSetting.data) {
        case "overlay":
            return <Vr />;
        case "update_software":
            return <Updater />;
        case "cuda_pack_prompt":
            return <CudaPackPrompt />;
        default:
            return null;
    }
};