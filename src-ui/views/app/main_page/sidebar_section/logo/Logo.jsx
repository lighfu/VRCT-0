import styles from "./Logo.module.scss";

export const Logo = () => {
    return (
        <div className={styles.container}>
            <LogoBox />
        </div>
    );
};


import vrct0_logo from "@images/vrct0_logo_stacked_for_dark.png";
import vrct0_icon from "@images/vrct0_icon.png";
import { useIsMainPageCompactMode } from "@logics_main";

export const LogoBox = () => {
    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    if (currentIsMainPageCompactMode.data === true) {
        return <img src={vrct0_icon} className={styles.logo_icon} alt="VRCT-0 icon" />;
    } else {
        return <img src={vrct0_logo} className={styles.logo} alt="VRCT-0 logo" />;
    }
};
