import { useStore_IsVrctAvailable } from "@store";
import { useNotificationStatus } from "@logics_common";
import { HomepageLinkButton } from "@common_components";
import { vrct0_issues_url } from "@ui_configs";

export const useIsVrctAvailable = () => {
    const { currentIsVrctAvailable, updateIsVrctAvailable } = useStore_IsVrctAvailable();
    const { showNotification_Success, showNotification_Error } = useNotificationStatus();

    const handleAiModelsAvailability = (is_ai_models_available) => {
        if (is_ai_models_available === false) {
            updateIsVrctAvailable(false);
            const ErrorComponent = () => {
                return (
                    <div>
                        <p>AI models have not been detected. Check the network connection and restart VRCT-0 (they are normally downloaded automatically).</p>
                        <p>If this error keeps happening, report it here:</p>
                        <HomepageLinkButton homepage_link={vrct0_issues_url} />
                    </div>
                );
            };
            showNotification_Error(ErrorComponent, { hide_duration: null });
        }
    };

    return {
        currentIsVrctAvailable,
        updateIsVrctAvailable,

        handleAiModelsAvailability,
    };
};