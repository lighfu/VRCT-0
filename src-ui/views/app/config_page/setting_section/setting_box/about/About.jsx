import { useState } from "react";
import { useI18n } from "@useI18n";
import { useSoftwareVersion, useResolvedUiTheme } from "@logics_common";
import { useAppearance } from "@logics_configs";
import {
    vrct0_issues_url,
    vrct0_releases_url,
    vrct0ReadmeUrl,
    original_vrct_url,
} from "@ui_configs";
import license_text from "@root/LICENSE?raw";
import vrct0_logo from "@images/vrct0_logo_stacked.png";
import vrct0_logo_for_dark from "@images/vrct0_logo_stacked_for_dark.png";
import ExternalLinkSvg from "@images/external_link.svg?react";
import { SectionLabelComponent, SettingRow } from "../_components";
import styles from "./About.module.scss";

// 設定の「VRCT-0 について」。VRCT-0 が元の VRCT とは別に開発している非公式の派生版であること、
// 不具合の報告先 (VRCT-0 の GitHub Issues)、元の VRCT へのクレジットとライセンスを出す。
export const About = () => {
    const { t } = useI18n();
    const { currentUiLanguage } = useAppearance();
    const { currentSoftwareVersion } = useSoftwareVersion();
    const { is_light } = useResolvedUiTheme();
    const [is_license_open, setIsLicenseOpen] = useState(false);

    return (
        <div className={styles.container}>
            <div className={styles.hero}>
                <img src={is_light ? vrct0_logo : vrct0_logo_for_dark} className={styles.logo} alt="VRCT-0" />
                <p className={styles.version}>{currentSoftwareVersion.data}</p>
                <p className={styles.lead}>{t("config_page.about.lead")}</p>
            </div>

            <SettingRow label={t("config_page.about.report.label")} desc={t("config_page.about.report.desc")}>
                <LinkButton href={vrct0_issues_url} label={t("config_page.about.report.button")} is_primary={true} />
            </SettingRow>
            <SettingRow label={t("config_page.about.guide.label")} desc={t("config_page.about.guide.desc")}>
                <LinkButton href={vrct0ReadmeUrl(currentUiLanguage.data)} label={t("config_page.about.guide.button")} />
            </SettingRow>
            <SettingRow label={t("config_page.about.releases.label")} desc={t("config_page.about.releases.desc")}>
                <LinkButton href={vrct0_releases_url} label={t("config_page.about.releases.button")} />
            </SettingRow>

            <div className={styles.section}>
                <SectionLabelComponent label={t("config_page.about.original.label")} />
                <p className={styles.credit}>{t("config_page.about.original.credit")}</p>
            </div>
            <SettingRow label={t("config_page.about.original.page_label")} desc={t("config_page.about.original.page_desc")}>
                <LinkButton href={original_vrct_url} label={t("config_page.about.original.button")} />
            </SettingRow>
            <SettingRow label={t("config_page.about.license.label")} desc={t("config_page.about.license.desc")}>
                <button
                    type="button"
                    className={styles.button}
                    onClick={() => setIsLicenseOpen((open) => !open)}
                    aria-expanded={is_license_open}
                >
                    {is_license_open ? t("config_page.about.license.hide") : t("config_page.about.license.show")}
                </button>
            </SettingRow>
            {is_license_open && <pre className={styles.license}>{license_text}</pre>}

            <p className={styles.disclaimer}>{t("config_page.about.vrchat_disclaimer")}</p>
        </div>
    );
};

const LinkButton = ({ href, label, is_primary = false }) => (
    <a className={is_primary ? styles.button_primary : styles.button} href={href} target="_blank" rel="noreferrer">
        <span>{label}</span>
        <ExternalLinkSvg className={styles.external_link_svg} />
    </a>
);
