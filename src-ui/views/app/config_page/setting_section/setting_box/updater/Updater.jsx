import { useEffect, useRef } from "react";
import { useI18n } from "@useI18n";
import styles from "./Updater.module.scss";

import { useAppUpdate, useSoftwareVersion, useWindow } from "@logics_common";
import { useUpdater } from "@logics_configs";
import { SectionLabelComponent, LabelComponent, RadioButton } from "../_components";

import CheckMarkSvg from "@images/check_mark.svg?react";
import RefreshSvg from "@images/refresh.svg?react";

const RELEASES_URL = "https://github.com/lighfu/VRCT-0/releases";

const formatSize = (bytes) => {
    if (!bytes) return "";
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
};

export const Updater = () => {
    const { t } = useI18n();
    const { currentSoftwareVersion } = useSoftwareVersion();
    const { currentReleaseChannel, setReleaseChannel } = useUpdater();
    const { currentAppUpdate, checkAppUpdate, downloadAppUpdate, restartToApplyUpdate } = useAppUpdate();
    const { asyncCloseApp } = useWindow();
    const previous_channel = useRef(currentReleaseChannel.data);

    const channel = currentReleaseChannel.data ?? "stable";
    const update = currentAppUpdate.data ?? { status: "idle" };
    const is_busy = update.status === "checking" || update.status === "downloading";
    // Rust の更新役は checking / downloading / ready の間はチャンネル変更を無視するので、
    // その間はチャンネル選択も操作できないようにする。
    const is_channel_locked = update.status === "checking" || update.status === "downloading" || update.status === "ready";

    // チャンネルを切り替えたら、そのチャンネルで確かめ直す。
    useEffect(() => {
        if (previous_channel.current === currentReleaseChannel.data) return;
        previous_channel.current = currentReleaseChannel.data;
        if (currentReleaseChannel.state === "ok") checkAppUpdate(currentReleaseChannel.data, true);
    }, [currentReleaseChannel.data, currentReleaseChannel.state]);

    const onClickCheck = () => {
        if (is_busy) return;
        checkAppUpdate(channel, true);
    };

    const onClickRestartNow = async () => {
        if (await restartToApplyUpdate()) await asyncCloseApp();
    };

    const channel_options = [
        { id: "stable", label: t("update_modal.channel_stable") },
        { id: "beta", label: t("update_modal.channel_beta") },
    ];

    return (
        <div className={styles.container}>
            <SectionLabelComponent label={t("update_modal.title")} />

            <div className={styles.summary_container}>
                <UpdateStatus
                    update={update}
                    current_version={currentSoftwareVersion.data}
                    channel={channel}
                    onClickDownload={() => downloadAppUpdate()}
                    onClickRestartNow={onClickRestartNow}
                />
            </div>

            <div className={styles.subsection_head}>
                <SectionLabelComponent label={t("update_modal.channel_label")} />
                {update.status !== "not_installed" && (
                    <button className={styles.refresh_button} onClick={onClickCheck} disabled={is_busy}>
                        <RefreshSvg className={styles.refresh_svg} />
                        <span>{t("update_modal.refresh_button")}</span>
                    </button>
                )}
            </div>

            <div className={styles.rows}>
                <div className={styles.row}>
                    <LabelComponent label={t("update_modal.channel_label")} desc={t("update_modal.channel_desc")} />
                    <RadioButton
                        name="update_modal_channel"
                        options={channel_options}
                        checked_variable={{ state: currentReleaseChannel.state, data: channel }}
                        selectFunction={(id) => {
                            if (is_channel_locked) return;
                            setReleaseChannel(id);
                        }}
                    />
                </div>
            </div>
        </div>
    );
};

const UpdateStatus = ({ update, current_version, channel, onClickDownload, onClickRestartNow }) => {
    const { t } = useI18n();
    const channel_label = channel === "beta" ? t("update_modal.channel_beta") : t("update_modal.channel_stable");

    switch (update.status) {
        case "not_installed":
            return <p className={styles.status_text}>{t("update_modal.not_installed")}</p>;
        case "checking":
            return (
                <div className={styles.summary_loading_wrapper}>
                    <span className={styles.summary_loader} />
                    <p className={styles.status_text}>{t("update_modal.checking")}</p>
                </div>
            );
        case "available":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>
                        {t(update.is_downgrade ? "update_modal.available_downgrade" : "update_modal.available", {
                            version: update.version,
                            size: formatSize(update.size_bytes),
                        })}
                    </p>
                    <a className={styles.notes_link} href={`${RELEASES_URL}/tag/v${update.version}`} target="_blank" rel="noreferrer">
                        {t("update_modal.release_notes")}
                    </a>
                    <button className={styles.install_button} onClick={onClickDownload}>
                        {t("update_modal.download_button")}
                    </button>
                </div>
            );
        case "downloading":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>{t("update_modal.downloading", { percent: update.percent })}</p>
                    <div className={styles.progress_bar}>
                        <div className={styles.progress_fill} style={{ width: `${update.percent}%` }} />
                    </div>
                </div>
            );
        case "ready":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>{t("update_modal.ready", { version: update.version })}</p>
                    <button className={styles.install_button} onClick={onClickRestartNow} disabled={update.restart_requested}>
                        {t("update_modal.restart_now_button")}
                    </button>
                    <p className={styles.status_desc}>{t("update_modal.apply_on_exit_desc")}</p>
                </div>
            );
        case "failed":
            return (
                <div className={styles.status_block}>
                    <p className={styles.status_text}>
                        {t(update.stage === "download" ? "update_modal.download_failed" : "update_modal.check_failed", {
                            message: update.message,
                        })}
                    </p>
                    {update.stage === "download" && (
                        <button className={styles.install_button} onClick={onClickDownload}>
                            {t("update_modal.retry_button")}
                        </button>
                    )}
                </div>
            );
        default:
            return (
                <div className={styles.up_to_date_layout}>
                    <CheckMarkSvg className={styles.up_to_date_check_svg} />
                    <div className={styles.up_to_date_text}>
                        <div className={styles.up_to_date_version}>
                            {current_version}
                            <span className={styles.up_to_date_channel}>{channel_label}</span>
                        </div>
                        {update.status === "up_to_date" && (
                            <div className={styles.up_to_date_desc}>{t("update_modal.summary_up_to_date_desc")}</div>
                        )}
                    </div>
                </div>
            );
    }
};
