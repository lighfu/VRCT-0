import { useMemo } from "react";
import { useStore_UiTheme, useStore_UiThemeImages } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";
import {
    normalizeUiTheme,
    normalizeTheme,
    selectedTheme,
    resolveTheme,
    createCustomTheme,
    decodeShareCode,
    newImageId,
    MAX_CUSTOM_THEMES,
} from "../theme/theme_model.js";
import { DEFAULT_THEME_ID } from "../theme/presets.js";
import { saveCachedUiTheme } from "../theme/theme_cache.js";
import { fileToThemeImageDataUrl } from "../theme/theme_image.js";

// 色を選んでいるあいだは何度も変わるので、止まってから config へ保存する。
const SAVE_DELAY_MS = 400;
let save_timer = null;
// 読み込みを頼んだ背景画像 (同じ画像を何度も頼まないように)。
const requested_image_ids = new Set();

// テーマ (設定の「テーマ」タブ)。config の UI_THEME ({ selected_id, custom_themes }) を
// /get/data/ui_theme で受け取り、変えたら /set/data/ui_theme で返す。背景画像は別に
// /run/save_ui_theme_image と /run/load_ui_theme_image でやり取りする。
export const useUiTheme = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const { currentUiTheme, updateUiTheme } = useStore_UiTheme();
    const { currentUiThemeImages, updateUiThemeImages } = useStore_UiThemeImages();

    const scheduleSave = (ui_theme) => {
        clearTimeout(save_timer);
        save_timer = setTimeout(() => {
            save_timer = null;
            asyncStdoutToPython("/set/data/ui_theme", ui_theme);
            saveCachedUiTheme(ui_theme);
        }, SAVE_DELAY_MS);
    };

    // updater は今の UI_THEME を受け取って次の UI_THEME を返す。
    const changeUiTheme = (updater) => {
        updateUiTheme((old) => {
            const next = normalizeUiTheme(updater(old.data));
            scheduleSave(next);
            return next;
        });
    };

    // /get/data/ui_theme (起動時)。保存待ちの変更があるときは、そちらが新しいので受け取らない。
    const updateUiThemeFromBackend = (payload) => {
        if (save_timer !== null) return;
        const ui_theme = normalizeUiTheme(payload);
        updateUiTheme(ui_theme);
        saveCachedUiTheme(ui_theme);
    };

    const selectTheme = (id) => {
        changeUiTheme((ui_theme) => ({ ...ui_theme, selected_id: id }));
    };

    // source から自分のテーマを作って選ぶ。いっぱいで作れなければ false。
    const addCustomTheme = (source, name) => {
        if (currentUiTheme.data.custom_themes.length >= MAX_CUSTOM_THEMES) return false;
        const theme = createCustomTheme(source, name);
        changeUiTheme((ui_theme) => ({
            selected_id: theme.id,
            custom_themes: [...ui_theme.custom_themes, theme],
        }));
        return true;
    };

    // updater はテーマを受け取って、変えたテーマを返す。
    const updateCustomTheme = (id, updater) => {
        changeUiTheme((ui_theme) => ({
            ...ui_theme,
            custom_themes: ui_theme.custom_themes.map((theme) =>
                theme.id === id ? { ...normalizeTheme(updater(theme)), id } : theme
            ),
        }));
    };

    const deleteCustomTheme = (id) => {
        changeUiTheme((ui_theme) => ({
            selected_id: ui_theme.selected_id === id ? DEFAULT_THEME_ID : ui_theme.selected_id,
            custom_themes: ui_theme.custom_themes.filter((theme) => theme.id !== id),
        }));
    };

    // 共有の文字列を取り込む。戻り値は "imported" / "invalid" / "full"。
    const importTheme = (code, fallback_name) => {
        const theme = decodeShareCode(code);
        if (!theme) return "invalid";
        return addCustomTheme(theme, theme.name || fallback_name) ? "imported" : "full";
    };

    // 画像を縮めて保存し、そのテーマの背景にする。画像として読めなければ例外。
    const setThemeImage = async (theme_id, file) => {
        const data_url = await fileToThemeImageDataUrl(file);
        const image_id = newImageId();
        updateUiThemeImages((old) => ({ ...old.data, [image_id]: data_url }));
        asyncStdoutToPython("/run/save_ui_theme_image", { image_id, data_url });
        updateCustomTheme(theme_id, (theme) => ({
            ...theme,
            backdrop: { ...theme.backdrop, type: "image", image: { ...theme.backdrop.image, id: image_id } },
        }));
    };

    const requestThemeImage = (image_id) => {
        if (!image_id || image_id in currentUiThemeImages.data || requested_image_ids.has(image_id)) return;
        requested_image_ids.add(image_id);
        asyncStdoutToPython("/run/load_ui_theme_image", image_id);
    };

    // /run/load_ui_theme_image の応答。data_url が null なら保存先に無かった。
    const updateUiThemeImage = (payload) => {
        if (!payload?.image_id) return;
        updateUiThemeImages((old) => ({ ...old.data, [payload.image_id]: payload.data_url ?? null }));
    };

    return {
        currentUiTheme,
        currentUiThemeImages,
        updateUiThemeFromBackend,
        selectTheme,
        addCustomTheme,
        updateCustomTheme,
        deleteCustomTheme,
        importTheme,
        setThemeImage,
        requestThemeImage,
        updateUiThemeImage,
    };
};

// いま選ばれているテーマと、画面に当てる形 (theme_model.js の resolveTheme)。
export const useResolvedUiTheme = () => {
    const { currentUiTheme } = useStore_UiTheme();
    const { currentUiThemeImages } = useStore_UiThemeImages();
    const theme = selectedTheme(currentUiTheme.data);
    const image_id = theme.backdrop.type === "image" ? theme.backdrop.image.id : null;
    const data_url = image_id ? currentUiThemeImages.data[image_id] ?? null : null;
    return useMemo(() => ({ theme, image_id, ...resolveTheme(theme, data_url) }), [theme, image_id, data_url]);
};
