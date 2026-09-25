// テーマの色の計算に使う小さな道具。色はすべて "#rrggbb" か "#rrggbbaa" の文字列で持つ。
// 濃淡を作るときは、人の目の明るさに近い OKLab / OKLCH で計算する。
// (https://bottosson.github.io/posts/oklab/)

const HEX_RE = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

export const isHexColor = (value) => typeof value === "string" && HEX_RE.test(value.trim());

// "#rgb" "#rgba" "#rrggbb" "#rrggbbaa" を { r, g, b, a } (0〜1) にする。読めなければ null。
export const parseHex = (value) => {
    if (!isHexColor(value)) return null;
    let hex = value.trim().slice(1);
    if (hex.length <= 4) hex = [...hex].map((c) => c + c).join("");
    const channel = (i) => parseInt(hex.slice(i, i + 2), 16) / 255;
    return { r: channel(0), g: channel(2), b: channel(4), a: hex.length === 8 ? channel(6) : 1 };
};

const toByte = (value) => Math.round(Math.min(1, Math.max(0, value)) * 255);
const byteHex = (value) => toByte(value).toString(16).padStart(2, "0");

// 不透明なら "#rrggbb"、透けるなら "#rrggbbaa"。
export const toHex = ({ r, g, b, a = 1 }) => {
    const alpha = toByte(a);
    return `#${byteHex(r)}${byteHex(g)}${byteHex(b)}${alpha === 255 ? "" : alpha.toString(16).padStart(2, "0")}`;
};

export const normalizeHex = (value) => {
    const rgb = parseHex(value);
    return rgb ? toHex(rgb) : null;
};

export const withAlpha = (hex, alpha) => {
    const rgb = parseHex(hex);
    return rgb ? toHex({ ...rgb, a: rgb.a * alpha }) : null;
};

export const withoutAlpha = (hex) => {
    const rgb = parseHex(hex);
    return rgb ? toHex({ ...rgb, a: 1 }) : null;
};

const srgbToLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const linearToSrgb = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

export const rgbToOklab = ({ r, g, b }) => {
    const lr = srgbToLinear(r);
    const lg = srgbToLinear(g);
    const lb = srgbToLinear(b);
    const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
    const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
    const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
    return {
        L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
        a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
        b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
    };
};

// 戻り値は sRGB の範囲外 (0 未満・1 超え) になりうる。範囲に収めるのは呼ぶ側。
export const oklabToRgb = ({ L, a, b }) => {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return {
        r: linearToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        g: linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        b: linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
    };
};

export const oklabToOklch = ({ L, a, b }) => ({ L, C: Math.hypot(a, b), H: Math.atan2(b, a) });
export const oklchToOklab = ({ L, C, H }) => ({ L, a: C * Math.cos(H), b: C * Math.sin(H) });

export const hexToOklab = (hex) => rgbToOklab(parseHex(hex));
export const hexToOklch = (hex) => oklabToOklch(hexToOklab(hex));

const GAMUT_EPSILON = 0.0005;
const isInGamut = ({ r, g, b }) => [r, g, b].every((c) => c >= -GAMUT_EPSILON && c <= 1 + GAMUT_EPSILON);

// sRGB で出せない色は、明るさと色相を保ったまま彩度だけを下げて収める。
export const oklabToHex = (lab, alpha = 1) => {
    const L = Math.min(1, Math.max(0, lab.L));
    let rgb = oklabToRgb({ ...lab, L });
    if (!isInGamut(rgb)) {
        const { C, H } = oklabToOklch(lab);
        let low = 0;
        let high = C;
        for (let i = 0; i < 24; i++) {
            const mid = (low + high) / 2;
            if (isInGamut(oklabToRgb(oklchToOklab({ L, C: mid, H })))) low = mid;
            else high = mid;
        }
        rgb = oklabToRgb(oklchToOklab({ L, C: low, H }));
    }
    return toHex({ ...rgb, a: alpha });
};

export const oklchToHex = (lch, alpha = 1) => oklabToHex(oklchToOklab(lch), alpha);

// WCAG の相対輝度とコントラスト比 (1〜21)。
const relativeLuminance = (hex) => {
    const { r, g, b } = parseHex(hex);
    return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
};

export const contrastRatio = (hexA, hexB) => {
    const la = relativeLuminance(hexA);
    const lb = relativeLuminance(hexB);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};

// against に対してコントラスト比が min_ratio に届くまで、色相を保って明るさだけを動かす。
export const ensureContrast = (hex, against, min_ratio) => {
    if (contrastRatio(hex, against) >= min_ratio) return hex;
    const lch = hexToOklch(hex);
    const direction = hexToOklab(against).L > 0.6 ? -1 : 1;
    for (let step = 1; step <= 50; step++) {
        const candidate = oklchToHex({ ...lch, L: lch.L + direction * step * 0.02 });
        if (contrastRatio(candidate, against) >= min_ratio) return candidate;
    }
    return direction < 0 ? "#000000" : "#ffffff";
};
