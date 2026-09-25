import { useStore_SelectedConfigTabId } from "@store";

import {
    Device,
    Appearance,
    Theme,
    Translation,
    Transcription,
    Others,
    AdvancedSettings,
    Vr,
    Hotkeys,
    About,
    Updater,
    Ocr,
} from "@setting_box";

export const SettingBox = () => {
    const { currentSelectedConfigTabId } = useStore_SelectedConfigTabId();
    switch (currentSelectedConfigTabId.data) {
        case "device":
            return <Device />;
        case "appearance":
            return <Appearance />;
        case "theme":
            return <Theme />;
        case "translation":
            return <Translation />;
        case "transcription":
            return <Transcription />;
        case "others":
            return <Others />;
        case "vr":
            return <Vr />;
        case "hotkeys":
            return <Hotkeys />;
        case "advanced_settings":
            return <AdvancedSettings />;
        case "updater":
            return <Updater />;
        case "ocr":
            return <Ocr />;
        case "about":
            return <About />;

        default:
            return null;
    }
};