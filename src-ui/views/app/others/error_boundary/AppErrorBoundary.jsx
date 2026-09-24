import { useRef, useState } from "react";
import { ErrorBoundary } from "react-error-boundary";

import CopySvg from "@images/copy.svg?react";
import CheckMarkSvg from "@images/check_mark.svg?react";
import ExternalLinkSvg from "@images/external_link.svg?react";

import { ContactsContainer } from "./contacts_container/ContactsContainer";

import {
    useWindow,
    useAppUpdate,
    useAppUpdateStateListener,
    useSoftwareVersion,
    useCopyToClipboard,
} from "@logics_common";
import { CloseButton } from "@common_components";

import styles from "./AppErrorBoundary.module.scss";

const VRCT_STATUS_URL = "https://misyaguziya.github.io/VRCT-Docs/docs/faq/#vrct-status";

export const AppErrorBoundary = ({children}) => {
    const [errorInfo, setErrorInfo] = useState(null);

    return (
        <ErrorBoundary
            onError={(error, info) => setErrorInfo(info)}
            fallbackRender={({ error }) => (
                <ErrorContainer error={error} errorInfo={errorInfo} />
            )
        }>
            {children}
        </ErrorBoundary>
    );
};

const ErrorContainer = ({error, errorInfo}) => {
    const { asyncCloseApp } = useWindow();
    const { currentSoftwareVersion } = useSoftwareVersion();
    const { is_copied, copyToClipboard } = useCopyToClipboard({ show_error_notification: false });

    const formatted_stack = error ? formatStackTrace(error.stack) : "Unknown error";
    const app_version = currentSoftwareVersion?.data || "Unknown";

    const error_log_text = [
        `Version: ${app_version}`,
        `Date: ${new Date().toISOString().replace('T', ' ').split('.')[0]}`,
        "",
        "=== Error Stack ===",
        formatted_stack,
        "",
        "=== Component Stack ===",
        errorInfo?.componentStack ? formatStackTrace(errorInfo.componentStack) : "Not available",
    ].join("\n");

    const onCopyErrorLog = () => {
        copyToClipboard(error_log_text);
    };

    return (
        <div className={styles.container}>
            <div className={styles.drag_able_area} data-tauri-drag-region></div>
            <CloseButton variant="active_error" onClick={asyncCloseApp} />
            <div className={styles.wrapper}>
                <p className={styles.error_message}>An error occurred. Please restart VRCT or contact the developers.</p>
                <SafeActionButtons />
                {error ?
                    <div className={styles.error_detail_container}>
                        <div className={styles.error_stack_container}>
                            <p className={styles.error_stack}>
                                {error_log_text}
                            </p>
                        </div>
                        <button className={styles.copy_error_message_button} onClick={onCopyErrorLog}>
                            <p className={styles.copy_text}>Copy</p>
                            {is_copied
                                ? <CheckMarkSvg className={styles.check_mark_svg}/>
                                : <CopySvg className={styles.copy_svg}/>
                            }
                        </button>
                    </div>
                : null}
                <ContactsContainer />
            </div>
        </div>
    );
};


const SafeActionButtons = () => {
    try {
        return <ActionButtons />;
    } catch {
        return null;
    }
};

const ActionButtons = () => {
    const { currentAppUpdate, downloadAppUpdate, restartToApplyUpdate } = useAppUpdate();
    const { asyncCloseApp } = useWindow();
    // エラー画面では AppUpdateController が外れて状態が届かなくなるので、ここで受け取る。
    // 受け取らないと「更新」を押しても表示が「ダウンロード中」から先に進まない。
    useAppUpdateStateListener();
    const update = currentAppUpdate?.data;
    // ダウンロードに失敗したときも、ここから再試行できるようにする。
    const status = update?.status === "failed" && update?.stage === "download" ? "download_failed" : update?.status;
    const is_busy_ref = useRef(false);
    const [is_busy, setIsBusy] = useState(false);

    const onClickUpdate = async () => {
        if (is_busy_ref.current) return;
        is_busy_ref.current = true;
        setIsBusy(true);
        try {
            if (status === "available" || status === "download_failed") {
                await downloadAppUpdate();
            } else if (status === "ready" && (await restartToApplyUpdate())) {
                await asyncCloseApp();
                return;
            }
        } catch (e) {
            console.error("[AppErrorBoundary] Update failed:", e);
        } finally {
            is_busy_ref.current = false;
            setIsBusy(false);
        }
    };

    const labels = {
        available: "Update Available — Update Now",
        downloading: "Downloading update...",
        ready: "Restart to Update",
        download_failed: "Update Failed — Retry",
    };

    return (
        <div className={styles.action_buttons_container}>
            {labels[status] && (
                <button className={styles.update_button} onClick={onClickUpdate} disabled={status === "downloading" || is_busy}>
                    {labels[status]}
                </button>
            )}
            <a className={styles.status_link_button} href={VRCT_STATUS_URL} target="_blank" rel="noreferrer">
                <span>Check VRCT Status</span>
                <ExternalLinkSvg className={styles.external_link_svg} />
            </a>
        </div>
    );
};

const formatStackTrace = (stack) => {
    if (!stack) return "";
    // フルパスの除去（例として window.location.origin や絶対パス部分を削除）
    const formatted = stack.replace(new RegExp(window.location.origin, "g"), "");

    return formatted;
};