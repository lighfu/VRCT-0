import { useState } from "react";
import { useI18n } from "@useI18n";
import { useCudaPack } from "@logics_common";
import { LabelComponent } from "../index";
import styles from "./CudaPack.module.scss";

// 翻訳と文字起こしのデバイス欄の下に出す「GPU 部品」欄。GPU が無ければ出さない。
export const CudaPack = () => {
    const { t } = useI18n();
    const { currentCudaPack, downloadCudaPack, removeCudaPack, restartApp } = useCudaPack();
    const [is_restarting, setIsRestarting] = useState(false);
    const { status, progress } = currentCudaPack.data;

    if (status === "no_gpu") return null;

    const onClickRestart = async () => {
        if (is_restarting) return;
        setIsRestarting(true);
        try {
            await restartApp();
        } catch (error) {
            console.error("[CudaPack]", error);
            setIsRestarting(false);
        }
    };

    const renderControl = () => {
        switch (status) {
            case "driver_too_old":
                return <p className={styles.status_text}>{t("config_page.common.cuda_pack.driver_too_old")}</p>;
            case "not_installed":
                return (
                    <button className={styles.button} onClick={downloadCudaPack}>
                        {t("config_page.common.cuda_pack.install_button")}
                    </button>
                );
            case "downloading": {
                const percent = Math.round((progress ?? 0) * 100);
                return (
                    <div className={styles.progress_block}>
                        <p className={styles.status_text}>{t("config_page.common.cuda_pack.downloading", { percent })}</p>
                        <div className={styles.progress_bar}>
                            <div className={styles.progress_fill} style={{ width: `${percent}%` }} />
                        </div>
                    </div>
                );
            }
            case "installed_restart_required":
                return (
                    <button className={styles.button} onClick={onClickRestart} disabled={is_restarting}>
                        {t("config_page.common.cuda_pack.restart_to_use_button")}
                    </button>
                );
            case "installed":
                return (
                    <button className={styles.button_secondary} onClick={removeCudaPack}>
                        {t("config_page.common.cuda_pack.remove_button")}
                    </button>
                );
            case "remove_pending":
                return (
                    <div className={styles.progress_block}>
                        <p className={styles.status_text}>{t("config_page.common.cuda_pack.remove_pending")}</p>
                        <button className={styles.button} onClick={onClickRestart} disabled={is_restarting}>
                            {t("config_page.common.cuda_pack.restart_button")}
                        </button>
                    </div>
                );
            default:
                return null;
        }
    };

    return (
        <div className={styles.container}>
            <LabelComponent label={t("config_page.common.cuda_pack.label")} desc={t("config_page.common.cuda_pack.desc")} />
            <div className={styles.control}>{renderControl()}</div>
        </div>
    );
};
