import { useI18n } from "@useI18n";
import { useCudaPack } from "@logics_common";
import { useStore_OpenedQuickSetting } from "@store";
import styles from "./CudaPackPrompt.module.scss";

// 初回起動で 1 回だけ出す「GPU 部品を導入しますか？」。
export const CudaPackPrompt = () => {
    const { t } = useI18n();
    const { downloadCudaPack, notifyCudaPackDownloadStarted } = useCudaPack();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const close = () => updateOpenedQuickSetting("");

    return (
        <div className={styles.container}>
            <p className={styles.title}>{t("cuda_pack_prompt.title")}</p>
            <p className={styles.desc}>{t("cuda_pack_prompt.desc")}</p>
            <div className={styles.buttons}>
                <button className={styles.button} onClick={() => { downloadCudaPack(); notifyCudaPackDownloadStarted(); close(); }}>
                    {t("cuda_pack_prompt.install_button")}
                </button>
                <button className={styles.button_secondary} onClick={close}>
                    {t("cuda_pack_prompt.later_button")}
                </button>
            </div>
        </div>
    );
};
