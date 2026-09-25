import styles from "./Logo.module.scss";

export const Logo = () => {
    return (
        <div className={styles.container}>
            <LogoBox />
        </div>
    );
};


import vrct0_logo_for_dark from "@images/vrct0_logo_stacked_for_dark.png";
import vrct0_logo from "@images/vrct0_logo_stacked.png";
import vrct0_icon from "@images/vrct0_icon.png";
import { useIsMainPageCompactMode } from "@logics_main";
import { useResolvedUiTheme } from "@logics_common";

export const LogoBox = () => {
    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    const { is_light } = useResolvedUiTheme();
    if (currentIsMainPageCompactMode.data === true) {
        return <img src={vrct0_icon} className={styles.logo_icon} alt="VRCT-0 icon" />;
    } else {
        return <img src={is_light ? vrct0_logo : vrct0_logo_for_dark} className={styles.logo} alt="VRCT-0 logo" />;
    }
};
