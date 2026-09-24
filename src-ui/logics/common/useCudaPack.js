import { invoke } from "@tauri-apps/api/core";
import { useStore_CudaPack } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";
import { useI18n } from "@useI18n";
import { useNotificationStatus } from "./useNotificationStatus";

// GPU 部品 (CUDA の cuBLAS / cuDNN)。状態はサイドカー (models/cuda_pack.py) が持ち、
// /get/data/cuda_pack_status と /run/cuda_pack_status で送ってくる。
export const useCudaPack = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const { currentCudaPack, updateCudaPack } = useStore_CudaPack();
    const { showNotification_Success } = useNotificationStatus();
    const { t } = useI18n();

    const updateCudaPackStatus = (payload) => {
        updateCudaPack((old) => ({
            ...old.data,
            status: payload.status,
            prompted: payload.prompted,
            progress: payload.status === "downloading" ? (old.data.progress ?? 0) : null,
        }));
    };
    const updateCudaPackProgress = (payload) => {
        updateCudaPack((old) => ({ ...old.data, status: "downloading", progress: payload.progress }));
    };
    const downloadCudaPack = () => asyncStdoutToPython("/run/download_cuda_pack");
    const removeCudaPack = () => asyncStdoutToPython("/run/remove_cuda_pack");
    const markCudaPackPrompted = () => asyncStdoutToPython("/run/mark_cuda_pack_prompted");
    // 初回の問いかけから導入したときは、問いかけが閉じるだけで何も見えないので、進み具合の場所を知らせる。
    const notifyCudaPackDownloadStarted = () => {
        showNotification_Success(t("config_page.common.cuda_pack.download_started_notification"), {
            category_id: "cuda_pack_download_started",
            hide_duration: 8000,
        });
    };
    // /run/downloaded_cuda_pack。取得には数分かかるので、ほかの画面にいても分かるよう、閉じるまで出しておく。
    const notifyCudaPackInstalled = () => {
        showNotification_Success(t("config_page.common.cuda_pack.installed_notification"), {
            category_id: "cuda_pack_installed",
            hide_duration: null,
        });
    };
    // 閉じるときと同じく、サイドカーを先に終わらせてから起動し直す。
    const restartApp = async () => {
        asyncStdoutToPython("/run/shutdown");
        await new Promise((resolve) => setTimeout(resolve, 2000));
        await invoke("app_restart");
    };

    return {
        currentCudaPack,
        updateCudaPackStatus,
        updateCudaPackProgress,
        downloadCudaPack,
        removeCudaPack,
        markCudaPackPrompted,
        notifyCudaPackDownloadStarted,
        notifyCudaPackInstalled,
        restartApp,
    };
};
