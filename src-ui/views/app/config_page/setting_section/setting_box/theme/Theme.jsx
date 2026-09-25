import { useEffect, useState } from "react";
import { useI18n } from "@useI18n";
import { useUiTheme, useNotificationStatus } from "@logics_common";
import { PRESETS, selectedTheme, MAX_CUSTOM_THEMES } from "@logics/theme/theme_model.js";
import { SectionLabelComponent, SettingRow } from "../_components";
import { ThemeCard } from "./ThemeCard";
import { ThemeEditor } from "./ThemeEditor";
import { useThemeName } from "./useThemeName";
import styles from "./Theme.module.scss";

// 設定の「テーマ」タブ。
export const Theme = () => {
    const { t } = useI18n();
    const { currentUiTheme, currentUiThemeImages, selectTheme, requestThemeImage } = useUiTheme();
    const themeName = useThemeName();
    const ui_theme = currentUiTheme.data;
    const selected = selectedTheme(ui_theme);

    // 自分のテーマのカードに背景画像を出すため、使っている画像を読み込む。
    useEffect(() => {
        for (const theme of ui_theme.custom_themes) {
            if (theme.backdrop.type === "image") requestThemeImage(theme.backdrop.image.id);
        }
    }, [ui_theme.custom_themes]);

    const imageOf = (theme) => (theme.backdrop.type === "image" && currentUiThemeImages.data[theme.backdrop.image.id]) || null;

    const renderCards = (themes) => (
        <div className={styles.card_grid}>
            {themes.map((theme) => (
                <ThemeCard
                    key={theme.id}
                    theme={theme}
                    name={themeName(theme)}
                    image_data_url={imageOf(theme)}
                    is_selected={theme.id === selected.id}
                    onSelect={() => selectTheme(theme.id)}
                />
            ))}
        </div>
    );

    return (
        <>
            <div className={styles.gallery}>
                <SectionLabelComponent label={t("config_page.theme.presets_label")} />
                {renderCards(PRESETS)}
            </div>
            <div className={styles.gallery}>
                <SectionLabelComponent label={t("config_page.theme.custom_label")} />
                {ui_theme.custom_themes.length > 0
                    ? renderCards(ui_theme.custom_themes)
                    : <p className={styles.empty_text}>{t("config_page.theme.custom_empty")}</p>
                }
            </div>
            <ImportRow />
            {selected.is_preset
                ? <MakeFromPresetRow preset={selected} />
                : <ThemeEditor key={selected.id} theme={selected} />
            }
        </>
    );
};

const MakeFromPresetRow = ({ preset }) => {
    const { t } = useI18n();
    const { addCustomTheme } = useUiTheme();
    const { showNotification_Error } = useNotificationStatus();
    const themeName = useThemeName();

    const onClick = () => {
        const name = t("config_page.theme.copy_name", { name: themeName(preset) });
        if (!addCustomTheme(preset, name)) {
            showNotification_Error(t("config_page.theme.limit_reached", { max: MAX_CUSTOM_THEMES }));
        }
    };

    return (
        <SettingRow label={t("config_page.theme.make_from_preset.label")} desc={t("config_page.theme.make_from_preset.desc")}>
            <button type="button" className={styles.button} onClick={onClick}>
                {t("config_page.theme.make_from_preset.button")}
            </button>
        </SettingRow>
    );
};

const ImportRow = () => {
    const { t } = useI18n();
    const { importTheme } = useUiTheme();
    const { showNotification_Success, showNotification_Error } = useNotificationStatus();
    const [code, setCode] = useState("");

    const onImport = () => {
        const result = importTheme(code, t("config_page.theme.imported_name"));
        if (result === "imported") {
            setCode("");
            showNotification_Success(t("config_page.theme.import.imported"), { hide_duration: 2000 });
        } else if (result === "full") {
            showNotification_Error(t("config_page.theme.limit_reached", { max: MAX_CUSTOM_THEMES }));
        } else {
            showNotification_Error(t("config_page.theme.import.invalid"));
        }
    };

    return (
        <SettingRow label={t("config_page.theme.import.label")} desc={t("config_page.theme.import.desc")}>
            <input
                className={styles.text_input}
                value={code}
                placeholder={t("config_page.theme.import.placeholder")}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && code.trim()) onImport();
                }}
                spellCheck={false}
            />
            <button type="button" className={styles.button} onClick={onImport} disabled={!code.trim()}>
                {t("config_page.theme.import.button")}
            </button>
        </SettingRow>
    );
};
