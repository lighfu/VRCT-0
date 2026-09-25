import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { HexColorPicker, HexAlphaColorPicker } from "react-colorful";
import clsx from "clsx";
import { useI18n } from "@useI18n";
import { normalizeHex, withoutAlpha } from "@logics/theme/color.js";
import styles from "./ThemeColorPicker.module.scss";

// 打ち終わった形 (6 桁か 8 桁) だけをその場で当てる。"#ff0" のような途中の形は当てない。
const COMPLETE_HEX_RE = /^#?(?:[0-9a-f]{6}|[0-9a-f]{8})$/i;
const POPOVER_GAP_REM = 0.4;
const VIEWPORT_PADDING_REM = 0.8;

const remToPx = (rem) => rem * parseFloat(getComputedStyle(document.documentElement).fontSize);

// テーマの色を 1 つ選ぶ。見本を押すと色の選択が開く。
// allow_alpha なら不透明度も選べる。onReset を渡すと「自動に戻す」を出す (上書きしているときだけ)。
export const ThemeColorPicker = ({ label, color, onChange, onReset, allow_alpha = false, is_overridden = false }) => {
    const { t } = useI18n();
    const [is_open, setIsOpen] = useState(false);
    const [placement, setPlacement] = useState({ open_above: false, align_end: false });
    const [hex_input, setHexInput] = useState(color);
    const swatch_ref = useRef(null);
    const popover_ref = useRef(null);
    const hex_input_ref = useRef(null);

    // 打っている途中の文字を、当てた色で書き換えない。
    useEffect(() => {
        if (document.activeElement !== hex_input_ref.current) setHexInput(color);
    }, [color]);

    const emit = (value) => {
        const text = String(value).trim();
        const hex = normalizeHex(text.startsWith("#") ? text : `#${text}`);
        if (hex) onChange(allow_alpha ? hex : withoutAlpha(hex));
        return hex;
    };

    const commitHexInput = () => {
        if (!emit(hex_input)) setHexInput(color);
    };

    // 画面の端で切れないよう、入る側に開く。
    useLayoutEffect(() => {
        if (!is_open) return;
        const swatch = swatch_ref.current;
        const popover = popover_ref.current;
        if (!swatch || !popover) return;
        const rect = swatch.getBoundingClientRect();
        const gap = remToPx(POPOVER_GAP_REM);
        const padding = remToPx(VIEWPORT_PADDING_REM);
        const space_below = window.innerHeight - rect.bottom - gap - padding;
        const space_above = rect.top - gap - padding;
        setPlacement({
            open_above: space_below < popover.offsetHeight && space_above > space_below,
            align_end: window.innerWidth - rect.left - padding < popover.offsetWidth,
        });
    }, [is_open]);

    useEffect(() => {
        if (!is_open) return;
        const onMouseDown = (event) => {
            if (popover_ref.current?.contains(event.target) || swatch_ref.current?.contains(event.target)) return;
            setIsOpen(false);
        };
        const onKeyDown = (event) => {
            if (event.key === "Escape") setIsOpen(false);
        };
        document.addEventListener("mousedown", onMouseDown);
        document.addEventListener("keydown", onKeyDown);
        return () => {
            document.removeEventListener("mousedown", onMouseDown);
            document.removeEventListener("keydown", onKeyDown);
        };
    }, [is_open]);

    const Picker = allow_alpha ? HexAlphaColorPicker : HexColorPicker;

    return (
        <div className={styles.container}>
            <button
                ref={swatch_ref}
                type="button"
                className={clsx(styles.swatch_button, { [styles.is_open]: is_open })}
                onClick={() => setIsOpen((open) => !open)}
                title={`${label ?? ""} ${color}`.trim()}
                aria-label={label}
                aria-expanded={is_open}
            >
                <span className={styles.swatch} style={{ "--swatch_color": color }} />
                {is_overridden && <span className={styles.override_mark} />}
            </button>
            {label && <span className={styles.label}>{label}</span>}
            {is_open && (
                <div
                    ref={popover_ref}
                    className={clsx(
                        styles.popover,
                        placement.open_above ? styles.open_above : styles.open_below,
                        placement.align_end ? styles.align_end : styles.align_start,
                    )}
                >
                    <Picker color={color} onChange={emit} />
                    <div className={styles.popover_row}>
                        <input
                            ref={hex_input_ref}
                            className={styles.hex_input}
                            value={hex_input}
                            onChange={(event) => {
                                setHexInput(event.target.value);
                                if (COMPLETE_HEX_RE.test(event.target.value.trim())) emit(event.target.value);
                            }}
                            onBlur={commitHexInput}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") commitHexInput();
                            }}
                            spellCheck={false}
                            aria-label={label}
                        />
                        {onReset && is_overridden && (
                            <button type="button" className={styles.reset_button} onClick={onReset}>
                                {t("config_page.theme.details.reset")}
                            </button>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};
