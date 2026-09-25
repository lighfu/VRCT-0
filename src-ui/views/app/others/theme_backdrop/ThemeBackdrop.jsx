import { useResolvedUiTheme } from "@logics_common";
import { gradientCss } from "@logics/theme/theme_model.js";
import styles from "./ThemeBackdrop.module.scss";

// 窓いっぱいに敷くテーマの背景 (単色・グラデーション・画像)。パネルやメイン画面の地を
// 透かしたときに見える。「背景の不透明度」を下げると、この層ごと透けてデスクトップが見える。
export const ThemeBackdrop = () => {
    const { backdrop, backdrop_opacity } = useResolvedUiTheme();
    const { image } = backdrop;

    const fill_style = backdrop.type === "gradient"
        ? { background: gradientCss(backdrop.gradient) }
        : { backgroundColor: "var(--dark_888_color)" };

    // ぼかすと縁が透けるので、ぼかした分だけ外へ広げて縁を窓の外へ出す。
    const image_style = image && {
        backgroundImage: `url("${image.data_url}")`,
        opacity: image.strength / 100,
        filter: image.blur > 0 ? `blur(${image.blur}px)` : undefined,
        inset: `${-image.blur * 2}px`,
    };

    return (
        <div className={styles.backdrop} style={{ opacity: backdrop_opacity / 100 }} aria-hidden="true">
            <div className={styles.fill} style={fill_style} />
            {image_style && <div className={styles.image} style={image_style} />}
        </div>
    );
};
