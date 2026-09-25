import clsx from "clsx";
import { useI18n } from "@useI18n";
import { useStore_IsBreakPoint } from "@store";
import { LabelComponent } from "../_components";
import styles from "./Theme.module.scss";

// 設定の 1 行 (左に名前と説明、右に操作)。Templates の行と同じ見た目。
export const ThemeRow = ({ label, desc, children, column = false }) => {
    const { currentIsBreakPoint } = useStore_IsBreakPoint();
    return (
        <div className={clsx(styles.row, { [styles.column]: column || currentIsBreakPoint.data })}>
            <LabelComponent label={label} desc={desc} />
            <div className={styles.row_controls}>{children}</div>
        </div>
    );
};

// 画面に出すテーマの名前。用意されたテーマは言語ごとの名前、自分のテーマは付けた名前。
export const useThemeName = () => {
    const { t } = useI18n();
    return (theme) => theme.is_preset
        ? t(`config_page.theme.presets.${theme.key}`)
        : (theme.name || t("config_page.theme.untitled"));
};
