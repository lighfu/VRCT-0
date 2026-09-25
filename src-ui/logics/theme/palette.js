// テーマの基本の 5 色から、画面で使う色 (src-ui/views/app/_index_css/variables.css の
// CSS 変数) をすべて作る。
//
// 濃淡の作り方は今の配色 (DEFAULT_PALETTE) から割り出す。各段が基本の色から
// どれだけ明るい・暗いかを OKLab で測っておき、新しい基本の色に同じ差を当てる。
// そのため基本の色が既定のままなら、variables.css とまったく同じ色になる。

import {
    parseHex,
    toHex,
    withAlpha,
    hexToOklab,
    hexToOklch,
    oklabToHex,
    oklchToHex,
    ensureContrast,
    contrastRatio,
} from "./color.js";

// variables.css の色と同じ値。variables.css を変えたらここも合わせる。
export const DEFAULT_PALETTE = {
    primary_100_color: "#b7ded8",
    primary_150_color: "#a1d4cc",
    primary_200_color: "#8acac0",
    primary_250_color: "#76bfb4",
    primary_300_color: "#61b4a7",
    primary_350_color: "#55ac9e",
    primary_400_color: "#48a495",
    primary_450_color: "#429c8c",
    primary_500_color: "#3b9483",
    primary_550_color: "#398e7d",
    primary_600_color: "#368777",
    primary_650_color: "#347f6f",
    primary_700_color: "#317767",
    primary_750_color: "#2f6f60",
    primary_800_color: "#2c6759",
    primary_900_color: "#214b3f",

    sent_400_color: "#6197b4",
    received_300_color: "#a861b4",

    error_bc_color: "#bb4448",
    error_bc_active_color: "#9c3938",
    warning_color: "#cb944f",
    warning_bc_color: "#cf7b1b",

    dark_basic_text_color: "#f2f2f2",
    dark_100_color: "#f5f7fb",
    dark_200_color: "#f1f2f6",
    dark_300_color: "#e9eaee",
    dark_350_color: "#d8d9dd",
    dark_400_color: "#c7c8cc",
    dark_450_color: "#b8b9bd",
    dark_500_color: "#a9aaae",
    dark_550_color: "#949599",
    dark_600_color: "#7f8084",
    dark_650_color: "#75767a",
    dark_700_color: "#6a6c6f",
    dark_725_color: "#636467",
    dark_750_color: "#5b5c5f",
    dark_775_color: "#535457",
    dark_800_color: "#4b4c4f",
    dark_825_color: "#434447",
    dark_850_color: "#3a3b3e",
    dark_863_color: "#36373a",
    dark_875_color: "#323336",
    dark_888_color: "#2e2f32",
    dark_900_color: "#292a2d",
    dark_925_color: "#242528",
    dark_950_color: "#1f2022",
    dark_975_color: "#1a1b1d",
    dark_1000_color: "#151517",
};

export const DEFAULT_BASE = {
    accent: DEFAULT_PALETTE.primary_400_color,
    background: DEFAULT_PALETTE.dark_900_color,
    text: DEFAULT_PALETTE.dark_basic_text_color,
    sent: DEFAULT_PALETTE.sent_400_color,
    received: DEFAULT_PALETTE.received_300_color,
};

export const BASE_COLOR_KEYS = ["accent", "background", "text", "sent", "received"];

// 「詳細」で 1 色ずつ上書きできる色。並びは編集画面の並び。
export const ACCENT_VARIABLES = Object.keys(DEFAULT_PALETTE).filter((name) => name.startsWith("primary_"));
export const NEUTRAL_VARIABLES = Object.keys(DEFAULT_PALETTE).filter((name) => name.startsWith("dark_"));
export const MESSAGE_VARIABLES = ["sent_400_color", "received_300_color"];
export const STATUS_VARIABLES = ["error_bc_color", "error_bc_active_color", "warning_color", "warning_bc_color"];
export const EDITABLE_VARIABLES = [...ACCENT_VARIABLES, ...NEUTRAL_VARIABLES, ...MESSAGE_VARIABLES, ...STATUS_VARIABLES];

// 背景の明るさがこれを超えたら明るいテーマとして扱う (OKLab の L)。
const LIGHT_BACKGROUND_THRESHOLD = 0.6;
export const isLightBackground = (hex) => hexToOklab(hex).L > LIGHT_BACKGROUND_THRESHOLD;

// ---- アクセント色の濃淡 ------------------------------------------------------
// 暗いテーマでは、小さい段 (100〜300) が暗い背景の上の文字やアイコン、大きい段
// (500〜800) が明るい文字を載せるボタンの塗り。明るいテーマでは明るさの差を反転し、
// 小さい段を濃く、大きい段を淡くして、同じ役割のまま読めるようにする。
const DEFAULT_ACCENT_LCH = hexToOklch(DEFAULT_BASE.accent);
const ACCENT_STEPS = ACCENT_VARIABLES.map((name) => {
    const lch = hexToOklch(DEFAULT_PALETTE[name]);
    return {
        name,
        dL: lch.L - DEFAULT_ACCENT_LCH.L,
        c_ratio: lch.C / DEFAULT_ACCENT_LCH.C,
        dH: lch.H - DEFAULT_ACCENT_LCH.H,
    };
});

// ボタンの塗り (450 以降) は、上に載る文字 (dark_basic_text) が読める濃さにする。
// 明るいアクセント色だと塗りも明るくなりすぎるので、塗りの段をまとめてずらす
// (段どうしの差は保つので、押したとき・重ねたときの色の違いは残る)。
const FILL_STEP_MIN = 450;
const FILL_TEXT_MIN_CONTRAST = 4;
const isFillStep = (name) => Number(name.split("_")[1]) >= FILL_STEP_MIN;

const generateAccent = (accent, text, is_light) => {
    if (accent === DEFAULT_BASE.accent && text === DEFAULT_BASE.text && !is_light) {
        return Object.fromEntries(ACCENT_VARIABLES.map((name) => [name, DEFAULT_PALETTE[name]]));
    }
    const seed = hexToOklch(accent);
    const build = (fill_shift) => Object.fromEntries(ACCENT_STEPS.map(({ name, dL, c_ratio, dH }) => {
        const L = seed.L + (is_light ? -dL : dL) + (isFillStep(name) ? fill_shift : 0);
        return [name, oklchToHex({ L: Math.min(0.98, Math.max(0.05, L)), C: seed.C * c_ratio, H: seed.H + dH })];
    }));
    // 暗いテーマは塗りを暗く、明るいテーマは塗りを明るくすると文字が読みやすくなる。
    const direction = hexToOklab(text).L > 0.5 ? -1 : 1;
    let colors = build(0);
    for (let step = 1; step <= 40 && contrastRatio(text, colors.primary_600_color) < FILL_TEXT_MIN_CONTRAST; step++) {
        colors = build(direction * step * 0.01);
    }
    return colors;
};

// ---- 背景と文字の濃淡 --------------------------------------------------------
// 各段を「背景 (dark_900) から文字 (dark_basic_text) までのどこか」として測る。
// t = 0 が背景、t = 1 が文字。dark_925〜1000 は背景よりさらに奥 (t < 0)、
// dark_100 は文字より手前 (t > 1)。今の配色のほのかな青みは、背景と文字を
// 結んだ線からのずれ (a, b の残り) として持っておき、新しい色にも足す。
const DEFAULT_BG_LAB = hexToOklab(DEFAULT_BASE.background);
const DEFAULT_TEXT_LAB = hexToOklab(DEFAULT_BASE.text);
const clamp01 = (value) => Math.min(1, Math.max(0, value));
const lerp = (from, to, t) => from + (to - from) * t;

const NEUTRAL_STEPS = NEUTRAL_VARIABLES.map((name) => {
    const lab = hexToOklab(DEFAULT_PALETTE[name]);
    const t = (lab.L - DEFAULT_BG_LAB.L) / (DEFAULT_TEXT_LAB.L - DEFAULT_BG_LAB.L);
    const tc = clamp01(t);
    return {
        name,
        t,
        rest_a: lab.a - lerp(DEFAULT_BG_LAB.a, DEFAULT_TEXT_LAB.a, tc),
        rest_b: lab.b - lerp(DEFAULT_BG_LAB.b, DEFAULT_TEXT_LAB.b, tc),
    };
});
const MIN_T = Math.min(...NEUTRAL_STEPS.map((step) => step.t));
const MAX_T = Math.max(...NEUTRAL_STEPS.map((step) => step.t));

const generateNeutral = (background, text) => {
    if (background === DEFAULT_BASE.background && text === DEFAULT_BASE.text) {
        return Object.fromEntries(NEUTRAL_VARIABLES.map((name) => [name, DEFAULT_PALETTE[name]]));
    }
    const bg = hexToOklab(background);
    const fg = hexToOklab(text);
    const span = fg.L - bg.L;
    const toward = Math.sign(span) || 1;

    // 背景より奥・文字より手前の段が 0〜1 の外へはみ出すときは、残りの幅に縮めて並べる。
    const room_behind = toward > 0 ? bg.L : 1 - bg.L;
    const room_beyond = toward > 0 ? 1 - fg.L : fg.L;
    const need_behind = Math.abs(MIN_T * span);
    const need_beyond = Math.abs((MAX_T - 1) * span);
    const shrink_behind = need_behind > room_behind ? room_behind / need_behind : 1;
    const shrink_beyond = need_beyond > room_beyond ? room_beyond / need_beyond : 1;

    return Object.fromEntries(NEUTRAL_STEPS.map(({ name, t, rest_a, rest_b }) => {
        let L;
        if (t < 0) L = bg.L + t * shrink_behind * span;
        else if (t > 1) L = fg.L + (t - 1) * shrink_beyond * span;
        else L = lerp(bg.L, fg.L, t);
        const tc = clamp01(t);
        return [name, oklabToHex({
            L,
            a: lerp(bg.a, fg.a, tc) + rest_a,
            b: lerp(bg.b, fg.b, tc) + rest_b,
        })];
    }));
};

// ---- まとめ ------------------------------------------------------------------
const darken = (hex, amount) => {
    const { r, g, b, a } = parseHex(hex);
    return toHex({ r: r * (1 - amount), g: g * (1 - amount), b: b * (1 - amount), a });
};

// 上書きを当てたあとの色から作る、透けた色などの派生の色。
const deriveVariables = (colors) => ({
    primary_600_color_44: withAlpha(colors.primary_600_color, 0x44 / 255),
    dark_550_color_22: withAlpha(colors.dark_550_color, 0x22 / 255),
    dark_825_color_cc: withAlpha(colors.dark_825_color, 0xcc / 255),
    dark_1000_color_66: withAlpha(colors.dark_1000_color, 0x66 / 255),
    dark_1000_color_aa: withAlpha(colors.dark_1000_color, 0xaa / 255),
    dark_1000_color_dd: withAlpha(colors.dark_1000_color, 0xdd / 255),
    // variables.css のコメントどおり「送信の色に黒を 10% 混ぜた色」。
    supporters_color_fuwa: darken(colors.sent_400_color, 0.1),
});

// base: { accent, background, text, sent, received } (どれも "#rrggbb")
// overrides: { primary_300_color: "#rrggbbaa", ... } (EDITABLE_VARIABLES の一部)
// 戻り値は CSS 変数名 (先頭の "--" なし) から色への対応。
export const buildPalette = (base, overrides = {}) => {
    const is_light = isLightBackground(base.background);
    const generated = {
        ...generateAccent(base.accent, base.text, is_light),
        ...generateNeutral(base.background, base.text),
        sent_400_color: base.sent,
        received_300_color: base.received,
        error_bc_color: DEFAULT_PALETTE.error_bc_color,
        error_bc_active_color: DEFAULT_PALETTE.error_bc_active_color,
        // 警告の文字は背景の上で読める濃さにする (明るいテーマで薄くなりすぎないように)。
        warning_color: ensureContrast(DEFAULT_PALETTE.warning_color, base.background, 3),
        warning_bc_color: DEFAULT_PALETTE.warning_bc_color,
    };
    const colors = { ...generated };
    for (const name of EDITABLE_VARIABLES) {
        if (overrides[name]) colors[name] = overrides[name];
    }
    return { colors: { ...colors, ...deriveVariables(colors) }, generated, is_light };
};

