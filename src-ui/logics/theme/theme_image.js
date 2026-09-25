// 背景に選ばれた画像を、保存・表示に向いた大きさに縮める。
// 画面より大きな写真をそのまま持つと config フォルダも起動時の読み込みも重くなるので、
// 長い辺を MAX_IMAGE_SIDE までにして WebP にする (WebP で書けなければ PNG になる)。

const MAX_IMAGE_SIDE = 1920;
const WEBP_QUALITY = 0.85;

export const THEME_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif,image/bmp";

// 画像として読めなければ例外。
export const fileToThemeImageDataUrl = async (file) => {
    const bitmap = await createImageBitmap(file);
    try {
        const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/webp", WEBP_QUALITY);
    } finally {
        bitmap.close?.();
    }
};
