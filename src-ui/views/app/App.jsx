import { useI18n } from "@useI18n";

import {
    KeyEventController,
    StartPythonController,
    GlobalHotKeyController,
    UiLanguageController,
    ConfigPageCloseTriggerController,
    UiSizeController,
    FontFamilyController,
    TransparencyController,
    CornerRadiusController,
    AppUpdateController,
    CudaPackPromptController,
    ThemeController,
} from "./_app_controllers";

import styles from "./App.module.scss";

import { MainPage } from "./main_page/MainPage";
import { ConfigPage } from "./config_page/ConfigPage";

import {
    WindowTitleBar,
    SplashComponent,
    ModalController,
    SnackbarController,
    AppErrorBoundary,
    ThemeBackdrop,
} from "./others";

import { useIsBackendReady, useIsVrctAvailable, useWindow } from "@logics_common";

export const App = () => {
    const { currentIsVrctAvailable } = useIsVrctAvailable();
    const { currentIsBackendReady } = useIsBackendReady();
    const { i18n } = useI18n();

    return (
        <div className={styles.container}>
            <ThemeController />
            <ThemeBackdrop />
            <AppErrorBoundary >
                <KeyEventController />
                <StartPythonController />
                <GlobalHotKeyController />
                <AppUpdateController />
                <CudaPackPromptController />
                <UiLanguageController />
                <ConfigPageCloseTriggerController />
                <UiSizeController />
                <FontFamilyController />
                <TransparencyController />
                <CornerRadiusController />

                {(currentIsBackendReady.data === false || currentIsVrctAvailable.data === false)
                    ? <SplashComponent />
                    : <Contents key={i18n.language} />
                }

                <SnackbarController />
            </AppErrorBoundary>
        </div>
    );
};

const Contents = () => {
    const { WindowGeometryController } = useWindow();
    return (
        <>
            <WindowGeometryController />

            <WindowTitleBar />
            <div className={styles.pages_wrapper}>
                <ConfigPage />
                <MainPage />
                <ModalController />
            </div>
        </>
    );
};