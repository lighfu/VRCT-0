import { useI18n } from "@useI18n";

// 画面に出すテーマの名前。用意されたテーマは言語ごとの名前、自分のテーマは付けた名前。
export const useThemeName = () => {
    const { t } = useI18n();
    return (theme) => theme.is_preset
        ? t(`config_page.theme.presets.${theme.key}`)
        : (theme.name || t("config_page.theme.untitled"));
};
