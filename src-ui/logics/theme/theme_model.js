// テーマの形をそろえる・検査する・共有の文字列にする。
//
// テーマは共有の文字列から他の人のものを取り込むこともあるので、画面に当てる前に
// 必ず normalizeTheme を通す。色は "#rrggbb(aa)" だけ、数は範囲内だけを残すので、
// CSS に url() などの任意の値が入ることはない。

import { normalizeHex, withoutAlpha } from "./color.js";
import { DEFAULT_BASE, BASE_COLOR_KEYS, EDITABLE_VARIABLES, buildPalette } from "./palette.js";
import { PRESET_THEMES, DEFAULT_THEME_ID } from "./presets.js";

export const BACKDROP_TYPES = ["solid", "gradient", "image"];
// config.py の UI_THEME_MAX_CUSTOM_THEMES と同じ。
export const MAX_CUSTOM_THEMES = 50;
export const MAX_THEME_NAME_LENGTH = 40;
export const MAX_GRADIENT_COLORS = 3;
export const MAX_IMAGE_BLUR = 20;

const THEME_ID_RE = /^[a-z0-9_]{1,64}$/;
const IMAGE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const SHARE_CODE_PREFIX = "VRCT0-THEME:1:";
const MAX_SHARE_CODE_LENGTH = 64 * 1024;

export const DEFAULT_GRADIENT = { angle: 135, colors: ["#2c6759", "#292a2d"] };
const DEFAULT_IMAGE = { id: null, strength: 100, blur: 0 };

export const DEFAULT_UI_THEME = { selected_id: DEFAULT_THEME_ID, custom_themes: [] };

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

const clampInt = (value, min, max, fallback) => {
    const number = Number(value);
    if (value === null || value === "" || !Number.isFinite(number)) return fallback;
    return Math.round(Math.min(max, Math.max(min, number)));
};

const normalizeBackdrop = (raw) => {
    const src = isObject(raw) ? raw : {};
    const gradient_src = isObject(src.gradient) ? src.gradient : {};
    const image_src = isObject(src.image) ? src.image : {};

    const colors = (Array.isArray(gradient_src.colors) ? gradient_src.colors : [])
        .map(normalizeHex)
        .filter(Boolean)
        .slice(0, MAX_GRADIENT_COLORS);

    return {
        type: BACKDROP_TYPES.includes(src.type) ? src.type : "solid",
        gradient: {
            angle: clampInt(gradient_src.angle, 0, 360, DEFAULT_GRADIENT.angle),
            colors: colors.length >= 2 ? colors : [...DEFAULT_GRADIENT.colors],
        },
        image: {
            id: typeof image_src.id === "string" && IMAGE_ID_RE.test(image_src.id) ? image_src.id : null,
            strength: clampInt(image_src.strength, 0, 100, DEFAULT_IMAGE.strength),
            blur: clampInt(image_src.blur, 0, MAX_IMAGE_BLUR, DEFAULT_IMAGE.blur),
        },
    };
};

export const normalizeTheme = (raw) => {
    const src = isObject(raw) ? raw : {};
    const base_src = isObject(src.base) ? src.base : {};
    const overrides_src = isObject(src.overrides) ? src.overrides : {};

    const base = Object.fromEntries(BASE_COLOR_KEYS.map((key) => [
        key,
        withoutAlpha(normalizeHex(base_src[key])) ?? DEFAULT_BASE[key],
    ]));
    const overrides = {};
    for (const name of EDITABLE_VARIABLES) {
        const color = normalizeHex(overrides_src[name]);
        if (color) overrides[name] = color;
    }

    const theme = {
        name: typeof src.name === "string" ? src.name.trim().slice(0, MAX_THEME_NAME_LENGTH) : "",
        base,
        overrides,
        backdrop: normalizeBackdrop(src.backdrop),
        backdrop_opacity: clampInt(src.backdrop_opacity, 0, 100, 100),
        panel_opacity: clampInt(src.panel_opacity, 0, 100, 100),
    };
    if (typeof src.id === "string" && THEME_ID_RE.test(src.id)) theme.id = src.id;
    return theme;
};

export const PRESETS = PRESET_THEMES.map((preset) => ({ ...normalizeTheme(preset), key: preset.key, is_preset: true }));

const isCustomThemeId = (id) => typeof id === "string" && id.startsWith("custom_") && THEME_ID_RE.test(id);

// config の UI_THEME ({ selected_id, custom_themes }) をそろえる。
export const normalizeUiTheme = (raw) => {
    const src = isObject(raw) ? raw : {};
    const seen = new Set();
    const custom_themes = [];
    for (const theme_src of Array.isArray(src.custom_themes) ? src.custom_themes : []) {
        const theme = normalizeTheme(theme_src);
        if (!isCustomThemeId(theme.id) || seen.has(theme.id)) continue;
        seen.add(theme.id);
        custom_themes.push(theme);
        if (custom_themes.length >= MAX_CUSTOM_THEMES) break;
    }
    const ids = new Set([...PRESETS.map((theme) => theme.id), ...seen]);
    return {
        selected_id: ids.has(src.selected_id) ? src.selected_id : DEFAULT_THEME_ID,
        custom_themes,
    };
};

export const findTheme = (ui_theme, id) =>
    PRESETS.find((theme) => theme.id === id)
    ?? ui_theme.custom_themes.find((theme) => theme.id === id)
    ?? PRESETS[0];

export const selectedTheme = (ui_theme) => findTheme(ui_theme, ui_theme.selected_id);

const randomId = (prefix) => {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    return prefix + [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
};
export const newCustomThemeId = () => randomId("custom_");
export const newImageId = () => randomId("img_");

// 用意されたテーマや取り込んだテーマから、自分のテーマを作る。
export const createCustomTheme = (source, name) => {
    const { id: _id, key: _key, is_preset: _is_preset, ...rest } = source;
    return normalizeTheme({ ...rest, id: newCustomThemeId(), name });
};

export const gradientCss = ({ angle, colors }) => `linear-gradient(${angle}deg, ${colors.join(", ")})`;

// 画面に当てる形にする。vars は CSS 変数名 ("--" 付き) から値への対応。
export const resolveTheme = (theme, image_data_url = null) => {
    const { colors, is_light } = buildPalette(theme.base, theme.overrides);
    const vars = Object.fromEntries(Object.entries(colors).map(([name, value]) => [`--${name}`, value]));
    const has_image = theme.backdrop.type === "image" && Boolean(image_data_url);
    return {
        vars,
        is_light,
        backdrop: {
            type: theme.backdrop.type === "image" && !has_image ? "solid" : theme.backdrop.type,
            gradient: theme.backdrop.gradient,
            image: has_image ? { ...theme.backdrop.image, data_url: image_data_url } : null,
        },
        backdrop_opacity: theme.backdrop_opacity,
        panel_opacity: theme.panel_opacity,
    };
};

// ---- 共有の文字列 ------------------------------------------------------------
// 背景画像は大きいので入れない (取り込んだ側で画像を選び直す)。
const toBase64 = (text) => {
    const bytes = new TextEncoder().encode(text);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
};

const fromBase64 = (base64) => {
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
};

export const encodeShareCode = (theme, name) => {
    const normalized = normalizeTheme(theme);
    const payload = {
        name: name ?? normalized.name,
        base: normalized.base,
        overrides: normalized.overrides,
        backdrop: { ...normalized.backdrop, image: { ...normalized.backdrop.image, id: null } },
        backdrop_opacity: normalized.backdrop_opacity,
        panel_opacity: normalized.panel_opacity,
    };
    return SHARE_CODE_PREFIX + toBase64(JSON.stringify(payload));
};

// 読めなければ null。
export const decodeShareCode = (text) => {
    if (typeof text !== "string") return null;
    const code = text.replace(/\s+/g, "");
    if (!code.startsWith(SHARE_CODE_PREFIX) || code.length > MAX_SHARE_CODE_LENGTH) return null;
    try {
        const parsed = JSON.parse(fromBase64(code.slice(SHARE_CODE_PREFIX.length)));
        if (!isObject(parsed)) return null;
        const { id: _id, ...rest } = parsed;
        return normalizeTheme(rest);
    } catch {
        return null;
    }
};
