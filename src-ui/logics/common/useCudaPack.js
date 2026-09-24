import { invoke } from "@tauri-apps/api/core";
import { useStore_CudaPack } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";

// GPU 部品 (CUDA の cuBLAS / cuDNN)。状態はサイドカー (models/cuda_pack.py) が持ち、
// /get/data/cuda_pack_status と /run/cuda_pack_status で送ってくる。
export const useCudaPack = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const { currentCudaPack, updateCudaPack } = useStore_CudaPack();

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
        restartApp,
    };
};
