// テーマの写しを WebView の localStorage に置く。本当の保存先は config.json
// (サイドカー) だが、サイドカーが起動するまでの画面 (起動中・モデル取得中) も
// 選んだテーマの色で出すために使う。読めない・書けないときは黙って既定に戻る。

import { normalizeUiTheme, DEFAULT_UI_THEME } from "./theme_model.js";

const UI_THEME_KEY = "vrct0_ui_theme";
const IMAGE_KEY = "vrct0_ui_theme_image";
// 背景画像の写しはこれより大きければ置かない (localStorage は 5MB ほどしか無い)。
const MAX_CACHED_IMAGE_CHARS = 3 * 1024 * 1024;

const read = (key) => {
    try {
        const text = window.localStorage.getItem(key);
        return text ? JSON.parse(text) : null;
    } catch {
        return null;
    }
};

const write = (key, value) => {
    try {
        if (value === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // 容量不足など。写しが無くても起動は既定の色で進む。
    }
};

export const loadCachedUiTheme = () => {
    const cached = read(UI_THEME_KEY);
    return cached ? normalizeUiTheme(cached) : DEFAULT_UI_THEME;
};

export const saveCachedUiTheme = (ui_theme) => write(UI_THEME_KEY, ui_theme);

// { [image_id]: data_url } の形で返す (store の UiThemeImages の初期値)。
export const loadCachedImages = () => {
    const cached = read(IMAGE_KEY);
    if (cached && typeof cached.id === "string" && typeof cached.data_url === "string") {
        return { [cached.id]: cached.data_url };
    }
    return {};
};

export const saveCachedImage = (image_id, data_url) => {
    if (!image_id || !data_url || data_url.length > MAX_CACHED_IMAGE_CHARS) {
        write(IMAGE_KEY, null);
        return;
    }
    write(IMAGE_KEY, { id: image_id, data_url });
};
