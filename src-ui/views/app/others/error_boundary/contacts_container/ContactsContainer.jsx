import styles from "./ContactsContainer.module.scss";
import github_icon from "@images/github_icon.png";
import { vrct0_issues_url } from "@ui_configs";

// エラー画面の問い合わせ先。VRCT-0 の不具合は VRCT-0 の GitHub Issues だけで受け付ける
// (元の VRCT の問い合わせフォームには送らせない)。
export const ContactsContainer = () => {
    return (
        <div className={styles.container}>
            <a className={styles.github_issues} href={vrct0_issues_url} target="_blank" rel="noreferrer">
                <img className={styles.contact_button_icon} src={github_icon} />
                <p className={styles.contact_button_label}>GitHub Issues (VRCT-0)</p>
            </a>
        </div>
    );
};
