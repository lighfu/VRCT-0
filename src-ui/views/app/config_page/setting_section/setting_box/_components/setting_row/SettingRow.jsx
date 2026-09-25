import clsx from "clsx";
import { useStore_IsBreakPoint } from "@store";
import { LabelComponent } from "../label_component/LabelComponent";
import styles from "./SettingRow.module.scss";

// 設定の 1 行 (左に名前と説明、右に操作)。Templates の行と同じ見た目で、右側に自由な中身を置ける。
export const SettingRow = ({ label, desc, children, column = false }) => {
    const { currentIsBreakPoint } = useStore_IsBreakPoint();
    return (
        <div className={clsx(styles.row, { [styles.column]: column || currentIsBreakPoint.data })}>
            <LabelComponent label={label} desc={desc} />
            <div className={styles.row_controls}>{children}</div>
        </div>
    );
};
