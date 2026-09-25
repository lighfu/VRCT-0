import { useI18n } from "@useI18n";
import styles from "./RightSideComponents.module.scss";
import RefreshSvg from "@images/refresh.svg?react";
import HelpSvg from "@images/help.svg?react";

import { useStore_OpenedQuickSetting } from "@store";
import {
    useAppUpdate,
    useIsOscAvailable,
} from "@logics_common";

import {
    useAppearance,
    useVr,
    useOthers,
} from "@logics_configs";
import { useMainFunction } from "@logics_main";
import { OpenQuickSettingButton } from "./_buttons/OpenQuickSettingButton";

import { vrct0ReadmeUrl } from "@ui_configs";

export const RightSideComponents = () => {
    const { currentUiLanguage } = useAppearance();

    return (
        <div className={styles.container}>
            <OpenForegroundQuickSetting />
            <OpenVrcMicMuteSyncQuickSetting />
            <OpenOverlayQuickSetting />
            <SoftwareUpdateAvailableButton />
            <a
                className={styles.help_and_info_button}
                href={vrct0ReadmeUrl(currentUiLanguage.data)}
                target="_blank"
                rel="noreferrer"
            >
                <HelpSvg className={styles.help_svg} />
            </a>
        </div>
    );
};

const OpenForegroundQuickSetting = () => {
    const { t } = useI18n();
    const { currentForegroundStatus, toggleForeground } = useMainFunction();

    return (
        <OpenQuickSettingButton
            label={t("main_page.foreground")}
            variable={currentForegroundStatus.data}
            state={currentForegroundStatus.state}
            onClickFunction={toggleForeground}
        />
    );
};

const OpenOverlayQuickSetting = () => {
    // const { t } = useI18n();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const {
        currentIsEnabledOverlaySmallLog,
        currentIsEnabledOverlayLargeLog,
    } = useVr();

    const onClickFunction = () => {
        updateOpenedQuickSetting("overlay");
    };

    const is_enable = currentIsEnabledOverlaySmallLog.data === true || currentIsEnabledOverlayLargeLog.data === true;

    return (
        <OpenQuickSettingButton
            label="Overlay(VR)"
            variable={is_enable}
            onClickFunction={onClickFunction}
        />
    );
};

const OpenVrcMicMuteSyncQuickSetting = () => {
    const { t } = useI18n();
    const { currentIsOscAvailable } = useIsOscAvailable();
    const { currentEnableVrcMicMuteSync, toggleEnableVrcMicMuteSync } = useOthers();

    const onClickFunction = () => {
        if (currentIsOscAvailable.data === false) return;
        toggleEnableVrcMicMuteSync();
    };

    return (
        <OpenQuickSettingButton
            label={t("config_page.others.vrc_mic_mute_sync.label")}
            variable={currentEnableVrcMicMuteSync.data}
            state={currentEnableVrcMicMuteSync.state}
            is_available={currentIsOscAvailable.data}
            onClickFunction={onClickFunction}
        />
    );
};

const SoftwareUpdateAvailableButton = () => {
    const { t } = useI18n();
    const { currentAppUpdate } = useAppUpdate();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();

    const status = currentAppUpdate.data?.status;
    // ダウンロードに失敗したときも出す (再試行ボタンは更新の画面にある)。
    const is_download_failed = status === "failed" && currentAppUpdate.data?.stage === "download";
    if (!["available", "downloading", "ready"].includes(status) && !is_download_failed) return null;

    return (
        <button className={styles.software_update_button} onClick={() => updateOpenedQuickSetting("update_software")}>
            <RefreshSvg className={styles.refresh_svg} />
            <p className={styles.software_update_label}>{t("main_page.update_available")}</p>
        </button>
    );
};