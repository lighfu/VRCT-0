import { useEffect, useRef } from "react";
import { useCudaPack, useIsBackendReady } from "@logics_common";
import { useStore_OpenedQuickSetting } from "@store";

// 起動して状態を受け取ったら、GPU があって部品が未導入で、まだ尋ねていなければ 1 回だけ尋ねる。
// 出した時点で「尋ねた」と記録する (どちらのボタンでも、閉じただけでも 2 回目は出さない)。
export const CudaPackPromptController = () => {
    const { currentCudaPack, markCudaPackPrompted } = useCudaPack();
    const { currentIsBackendReady } = useIsBackendReady();
    const { currentOpenedQuickSetting, updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const is_done = useRef(false);

    useEffect(() => {
        if (is_done.current) return;
        if (currentIsBackendReady.data !== true) return;
        // ほかのクイック設定 (アップデートや overlay など) が開いていたら、それを追い出さない。
        // 閉じられて次にこのエフェクトが走ったときにまた試す。
        if (currentOpenedQuickSetting.data !== "") return;
        const { status, prompted } = currentCudaPack.data;
        if (status !== "not_installed" || prompted !== false) return;
        is_done.current = true;
        markCudaPackPrompted();
        updateOpenedQuickSetting("cuda_pack_prompt");
    }, [currentIsBackendReady.data, currentCudaPack.data, currentOpenedQuickSetting.data]);

    return null;
};
