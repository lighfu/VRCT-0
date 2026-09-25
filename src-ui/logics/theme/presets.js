// 用意されたテーマ。名前は locales の config_page.theme.presets.<key>。
// 形は自分のテーマと同じ (theme_model.js の normalizeTheme を通す)。

import { DEFAULT_BASE } from "./palette.js";

// 初めて起動したときと、選んでいた自分のテーマを消したときのテーマ。
// config.py の UI_THEME の既定値と合わせる。
export const DEFAULT_THEME_ID = "preset_glass";

const preset = (key, theme) => ({ id: `preset_${key}`, key, name: "", overrides: {}, ...theme });

export const PRESET_THEMES = [
    preset("standard", {
        base: { ...DEFAULT_BASE },
    }),
    preset("night_sky", {
        base: { accent: "#6b9cf2", background: "#1c2233", text: "#e6ecf7", sent: "#7cc0e8", received: "#c3a0f2" },
    }),
    preset("sakura", {
        base: { accent: "#ec8fb0", background: "#2e2529", text: "#f6edf0", sent: "#8fb5dc", received: "#d8a8e6" },
    }),
    preset("amber", {
        base: { accent: "#e7a14c", background: "#2a2520", text: "#f4ece2", sent: "#86b7cf", received: "#d59ac0" },
    }),
    // VRCT-0 のロゴの紫と橙から。
    preset("lavender", {
        base: { accent: "#a594f5", background: "#252238", text: "#eeebf8", sent: "#7fb3ec", received: "#f2a06b" },
    }),
    // 黒地に白い文字。ボタンの塗りは白い文字が読める濃さの金色にする。
    preset("high_contrast", {
        base: { accent: "#ffd60a", background: "#000000", text: "#ffffff", sent: "#6ccfff", received: "#ff9be8" },
        overrides: {
            primary_450_color: "#b38f00",
            primary_500_color: "#9c7c00",
            primary_550_color: "#8c6f00",
            primary_600_color: "#806600",
            primary_650_color: "#735b00",
            primary_700_color: "#665000",
            primary_750_color: "#5c4800",
            primary_800_color: "#524000",
            primary_900_color: "#3d3000",
            dark_800_color: "#5c5c5c",
            dark_825_color: "#4d4d4d",
        },
    }),
    preset("light", {
        base: { accent: "#23897a", background: "#f4f5f7", text: "#1e2227", sent: "#2e6e9e", received: "#8c3d9c" },
    }),
    preset("light_sakura", {
        base: { accent: "#c9557d", background: "#fbf4f6", text: "#3b2a31", sent: "#3a6b9c", received: "#8b4fa5" },
    }),
    // グラデーションの背景に、少し透けたパネルを重ねる見本。
    preset("aurora", {
        base: { accent: "#56dbb8", background: "#141a26", text: "#eef5f4", sent: "#7cc3ff", received: "#d59cff" },
        backdrop: { type: "gradient", gradient: { angle: 160, colors: ["#0f3d45", "#1d1b4a", "#3d1747"] } },
        panel_opacity: 55,
    }),
    // 背景もパネルも半透明にして、デスクトップが透けて見える見本。
    preset("glass", {
        base: { accent: "#7cc4ff", background: "#1c1f24", text: "#f4f6f8", sent: "#8fd1ff", received: "#e0a8ff" },
        backdrop_opacity: 45,
        panel_opacity: 55,
    }),
];
