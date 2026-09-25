import { useEffect, useLayoutEffect } from "react";
import { useResolvedUiTheme, useUiTheme } from "@logics_common";
import { saveCachedImage } from "@logics/theme/theme_cache.js";

// 選ばれたテーマの色を html の CSS 変数に当てる。背景そのものは ThemeBackdrop が描く。
export const ThemeController = () => {
    const { vars, is_light, backdrop, backdrop_opacity, panel_opacity, image_id } = useResolvedUiTheme();
    const { currentUiThemeImages, requestThemeImage } = useUiTheme();

    // 背景を見せるテーマ (半透明・グラデーション・画像) では、メイン画面と設定画面の地を
    // 透明にする (variables.css の --page_alpha)。
    const shows_backdrop = backdrop.type !== "solid" || backdrop_opacity < 100 || panel_opacity < 100;

    // 描く前に当てて、前のテーマの色が一瞬見えないようにする。
    useLayoutEffect(() => {
        const root = document.documentElement;
        for (const [name, value] of Object.entries(vars)) {
            root.style.setProperty(name, value);
        }
        root.style.setProperty("--panel_alpha", `${panel_opacity}%`);
        root.style.setProperty("--page_alpha", shows_backdrop ? "0%" : "100%");
        root.dataset.themeMode = is_light ? "light" : "dark";
        if (shows_backdrop) root.dataset.translucent = "";
        else delete root.dataset.translucent;
    }, [vars, is_light, panel_opacity, shows_backdrop]);

    useEffect(() => {
        requestThemeImage(image_id);
    }, [image_id]);

    // 次の起動で、サイドカーより先に背景画像を出せるように写しを置く。
    const image_data_url = image_id ? currentUiThemeImages.data[image_id] : null;
    useEffect(() => {
        if (image_data_url === undefined) return; // まだ読み込み中
        saveCachedImage(image_id, image_data_url);
    }, [image_id, image_data_url]);

    return null;
};
