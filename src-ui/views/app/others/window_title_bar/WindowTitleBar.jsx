import { useWindow } from "@logics_common";
// import clsx from "clsx";
import styles from "./WindowTitleBar.module.scss";
import XMarkSvg from "@images/cancel.svg?react";
import SquareSvg from "@images/square.svg?react";
import LineSvg from "@images/line.svg?react";
import vrct0_logo_wide from "@images/vrct0_logo_wide_for_dark.png";

// ロゴの形だけを使い、色は CSS の background-color で付ける (今までの控えめな灰色の表示のまま)
const TITLE_LOGO_MASK = { maskImage: `url(${vrct0_logo_wide})`, WebkitMaskImage: `url(${vrct0_logo_wide})` };

export const WindowTitleBar = () => {
    const { asyncCloseApp, asyncToggleMaximizeApp, asyncMinimizeApp} = useWindow();

    return (
        <div className={styles.container}>
            <div className={styles.wrapper} data-tauri-drag-region>
                <div className={styles.title_wrapper}>
                    <div className={styles.title_logo} style={TITLE_LOGO_MASK} role="img" aria-label="VRCT-0"/>
                </div>

                <div className={styles.window_control_wrapper}>
                    <div className={styles.minimize_button} onClick={asyncMinimizeApp}>
                        <LineSvg className={styles.line_svg}/>
                    </div>
                    <div className={styles.maximize_button} onClick={asyncToggleMaximizeApp}>
                        <SquareSvg className={styles.square_svg}/>
                    </div>
                    <div className={styles.close_button} onClick={asyncCloseApp}>
                        <XMarkSvg className={styles.x_mark_svg}/>
                    </div>
                </div>
            </div>
        </div>
    );
};