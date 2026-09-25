import clsx from "clsx";
import styles from "./ConfigPage.module.scss";
import { useIsOpenedConfigPage } from "@logics_common";

import { Topbar } from "./topbar/Topbar.jsx";
import { SidebarSection } from "./sidebar_section/SidebarSection.jsx";
import { SettingSection } from "./setting_section/SettingSection.jsx";

export const ConfigPage = () => {
    const { currentIsOpenedConfigPage } = useIsOpenedConfigPage();

    return (
        <div className={clsx(styles.page, { [styles.is_hidden]: !currentIsOpenedConfigPage.data })}>
            <div className={styles.container}>
                <SidebarSection />
                <div className={styles.content_wrapper}>
                    <Topbar />
                    <div className={styles.main_container}>
                        <SettingSection />
                    </div>
                </div>
            </div>
        </div>
    );
};