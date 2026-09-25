import { useMemo } from "react";
import clsx from "clsx";
import { resolveTheme, gradientCss } from "@logics/theme/theme_model.js";
import styles from "./ThemeCard.module.scss";

const mixAlpha = (color, percent) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;

// テーマを選ぶカード。アプリの縮小版 (左にサイドバーと切り替え、右に送信・受信のログ) を
// そのテーマの色で描いて、選ぶ前に見た目が分かるようにする。
export const ThemeCard = ({ theme, name, is_selected, onSelect, image_data_url = null }) => {
    const resolved = useMemo(() => resolveTheme(theme, image_data_url), [theme, image_data_url]);
    const { vars, backdrop, backdrop_opacity, panel_opacity } = resolved;
    const shows_backdrop = backdrop.type !== "solid" || backdrop_opacity < 100 || panel_opacity < 100;

    const backdrop_style = {
        opacity: backdrop_opacity / 100,
        ...(backdrop.type === "gradient"
            ? { background: gradientCss(backdrop.gradient) }
            : { backgroundColor: vars["--dark_888_color"] }),
    };
    const panel = (name) => mixAlpha(vars[name], panel_opacity);

    return (
        <button
            type="button"
            className={clsx(styles.card, { [styles.is_selected]: is_selected })}
            onClick={onSelect}
            aria-pressed={is_selected}
        >
            <span className={clsx(styles.preview, { [styles.see_through]: backdrop_opacity < 100 })}>
                <span className={styles.backdrop} style={backdrop_style}>
                    {backdrop.image && (
                        <span
                            className={styles.image}
                            style={{
                                backgroundImage: `url("${backdrop.image.data_url}")`,
                                opacity: backdrop.image.strength / 100,
                            }}
                        />
                    )}
                </span>
                <span className={styles.window} style={{ backgroundColor: shows_backdrop ? "transparent" : vars["--dark_888_color"] }}>
                    <span className={styles.sidebar} style={{ backgroundColor: panel("--dark_850_color") }}>
                        <span className={styles.switch} style={{ backgroundColor: vars["--primary_400_color"] }}>
                            <span className={styles.knob_on} style={{ backgroundColor: vars["--dark_400_color"] }} />
                        </span>
                        <span className={styles.switch} style={{ backgroundColor: vars["--dark_775_color"] }}>
                            <span className={styles.knob_off} style={{ backgroundColor: vars["--dark_400_color"] }} />
                        </span>
                    </span>
                    <span className={styles.main} style={{ backgroundColor: panel("--dark_888_color") }}>
                        <span className={styles.log} style={{ backgroundColor: panel("--dark_900_color") }}>
                            <span className={styles.author} style={{ backgroundColor: vars["--sent_400_color"] }} />
                            <span className={styles.text} style={{ backgroundColor: vars["--dark_basic_text_color"] }} />
                            <span className={styles.author} style={{ backgroundColor: vars["--received_300_color"] }} />
                            <span className={clsx(styles.text, styles.short)} style={{ backgroundColor: vars["--dark_basic_text_color"] }} />
                        </span>
                    </span>
                </span>
            </span>
            <span className={styles.name}>{name}</span>
        </button>
    );
};
