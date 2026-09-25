import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { useI18n } from "@useI18n";
import { useUiTheme, useNotificationStatus, useCopyToClipboard } from "@logics_common";
import {
    buildPalette,
    BASE_COLOR_KEYS,
    ACCENT_VARIABLES,
    NEUTRAL_VARIABLES,
    STATUS_VARIABLES,
} from "@logics/theme/palette.js";
import {
    encodeShareCode,
    gradientCss,
    DEFAULT_GRADIENT,
    MAX_GRADIENT_COLORS,
    MAX_IMAGE_BLUR,
    MAX_THEME_NAME_LENGTH,
} from "@logics/theme/theme_model.js";
import { THEME_IMAGE_ACCEPT } from "@logics/theme/theme_image.js";
import { SectionLabelComponent } from "../_components";
import { SliderContainer, RadioButtonContainer } from "../_templates/Templates";
import { ThemeColorPicker } from "./ThemeColorPicker";
import { ThemeRow } from "./ThemeRow";
import styles from "./Theme.module.scss";

const PERCENT_MARKS = [0, 25, 50, 75, 100];

// 背景はパネル越しに見えるので、背景を見せる変更をしたのにパネルが不透明のままだと
// 何も変わらない。そのときはパネルを少し透かして、変わったことが分かるようにする。
const REVEAL_PANEL_OPACITY = 70;
const revealBackdrop = (theme) => (theme.panel_opacity === 100 ? { ...theme, panel_opacity: REVEAL_PANEL_OPACITY } : theme);

// 自分のテーマの編集欄。変えるとすぐ画面に当たり、止まってから保存される (useUiTheme)。
export const ThemeEditor = ({ theme }) => {
    const { t } = useI18n();
    const { updateCustomTheme } = useUiTheme();
    const update = (updater) => updateCustomTheme(theme.id, updater);

    return (
        <div className={styles.editor}>
            <div className={styles.editor_label}>
                <SectionLabelComponent label={t("config_page.theme.edit_label")} />
            </div>
            <NameRow theme={theme} update={update} />
            <BaseColorsRow theme={theme} update={update} />
            <BackdropRows theme={theme} update={update} />
            <SliderContainer
                label={t("config_page.theme.backdrop_opacity.label")}
                desc={t("config_page.theme.backdrop_opacity.desc")}
                variable={theme.backdrop_opacity}
                setterFunction={(value) => update((current) => {
                    const next = { ...current, backdrop_opacity: value };
                    return value < 100 ? revealBackdrop(next) : next;
                })}
                setter_timing="on_change"
                min={0}
                max={100}
                step={1}
                show_label_values={PERCENT_MARKS}
                marks_step={25}
                valueLabelFormat="value %"
            />
            <SliderContainer
                label={t("config_page.theme.panel_opacity.label")}
                desc={t("config_page.theme.panel_opacity.desc")}
                variable={theme.panel_opacity}
                setterFunction={(value) => update((current) => ({ ...current, panel_opacity: value }))}
                setter_timing="on_change"
                min={0}
                max={100}
                step={1}
                show_label_values={PERCENT_MARKS}
                marks_step={25}
                valueLabelFormat="value %"
            />
            <DetailsRows theme={theme} update={update} />
            <ShareRow theme={theme} />
            <DeleteRow theme={theme} />
        </div>
    );
};

const NameRow = ({ theme, update }) => {
    const { t } = useI18n();
    const [name, setName] = useState(theme.name);

    useEffect(() => {
        setName(theme.name);
    }, [theme.name]);

    // 打っている途中で前後の空白を削らないよう、確定したときに保存する。
    const commit = () => update((current) => ({ ...current, name }));

    return (
        <ThemeRow label={t("config_page.theme.name.label")}>
            <input
                className={styles.text_input}
                value={name}
                maxLength={MAX_THEME_NAME_LENGTH}
                placeholder={t("config_page.theme.untitled")}
                onChange={(event) => setName(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.nativeEvent.isComposing) event.currentTarget.blur();
                }}
            />
        </ThemeRow>
    );
};

const BaseColorsRow = ({ theme, update }) => {
    const { t } = useI18n();
    return (
        <ThemeRow label={t("config_page.theme.base_colors.label")} desc={t("config_page.theme.base_colors.desc")}>
            <div className={styles.swatch_row}>
                {BASE_COLOR_KEYS.map((key) => (
                    <ThemeColorPicker
                        key={key}
                        label={t(`config_page.theme.base_colors.${key}`)}
                        color={theme.base[key]}
                        onChange={(hex) => update((current) => ({ ...current, base: { ...current.base, [key]: hex } }))}
                    />
                ))}
            </div>
        </ThemeRow>
    );
};

const BackdropRows = ({ theme, update }) => {
    const { t } = useI18n();
    const { backdrop } = theme;

    const updateBackdrop = (changes) => update((current) => ({ ...current, backdrop: { ...current.backdrop, ...changes } }));

    const selectType = (type) => update((current) => {
        const next = { ...current, backdrop: { ...current.backdrop, type } };
        // はじめてグラデーションにするときは、テーマの色から作った 2 色で始める。
        if (type === "gradient" && current.backdrop.gradient.colors.join() === DEFAULT_GRADIENT.colors.join()) {
            const { colors } = buildPalette(current.base, current.overrides);
            next.backdrop.gradient = { ...current.backdrop.gradient, colors: [colors.primary_800_color, colors.dark_900_color] };
        }
        return type === "solid" ? next : revealBackdrop(next);
    });

    return (
        <>
            <RadioButtonContainer
                label={t("config_page.theme.backdrop.label")}
                desc={t("config_page.theme.backdrop.desc")}
                name="theme_backdrop_type"
                options={[
                    { id: "solid", label: t("config_page.theme.backdrop.solid") },
                    { id: "gradient", label: t("config_page.theme.backdrop.gradient") },
                    { id: "image", label: t("config_page.theme.backdrop.image") },
                ]}
                checked_variable={{ state: "ok", data: backdrop.type }}
                selectFunction={selectType}
            />
            {backdrop.type === "gradient" && <GradientRows theme={theme} updateBackdrop={updateBackdrop} />}
            {backdrop.type === "image" && <ImageRows theme={theme} updateBackdrop={updateBackdrop} />}
        </>
    );
};

const GradientRows = ({ theme, updateBackdrop }) => {
    const { t } = useI18n();
    const { gradient } = theme.backdrop;
    const setGradient = (changes) => updateBackdrop({ gradient: { ...gradient, ...changes } });
    const setColor = (index, hex) => setGradient({ colors: gradient.colors.map((color, i) => (i === index ? hex : color)) });

    return (
        <>
            <ThemeRow label={t("config_page.theme.gradient.colors")} desc={t("config_page.theme.gradient.colors_desc")}>
                <div className={styles.gradient_preview} style={{ "--gradient": gradientCss(gradient) }} />
                <div className={styles.swatch_row}>
                    {gradient.colors.map((color, index) => (
                        <ThemeColorPicker
                            key={index}
                            label={String(index + 1)}
                            color={color}
                            allow_alpha={true}
                            onChange={(hex) => setColor(index, hex)}
                        />
                    ))}
                </div>
                {gradient.colors.length < MAX_GRADIENT_COLORS
                    ? (
                        <button type="button" className={styles.button_secondary} onClick={() => setGradient({ colors: [...gradient.colors, gradient.colors.at(-1)] })}>
                            {t("config_page.theme.gradient.add_color")}
                        </button>
                    ) : (
                        <button type="button" className={styles.button_secondary} onClick={() => setGradient({ colors: gradient.colors.slice(0, -1) })}>
                            {t("config_page.theme.gradient.remove_color")}
                        </button>
                    )
                }
            </ThemeRow>
            <SliderContainer
                label={t("config_page.theme.gradient.angle")}
                variable={gradient.angle}
                setterFunction={(value) => setGradient({ angle: value })}
                setter_timing="on_change"
                min={0}
                max={360}
                step={5}
                show_label_values={[0, 90, 180, 270, 360]}
                marks_step={90}
                valueLabelFormat="value°"
            />
        </>
    );
};

const ImageRows = ({ theme, updateBackdrop }) => {
    const { t } = useI18n();
    const { currentUiThemeImages, setThemeImage } = useUiTheme();
    const { showNotification_Error } = useNotificationStatus();
    const [is_reading, setIsReading] = useState(false);
    const input_ref = useRef(null);
    const { image } = theme.backdrop;
    const data_url = image.id ? currentUiThemeImages.data[image.id] : null;
    const setImage = (changes) => updateBackdrop({ image: { ...image, ...changes } });

    const onFileChange = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        setIsReading(true);
        try {
            await setThemeImage(theme.id, file);
        } catch (error) {
            console.error("[ThemeEditor] failed to read image", error);
            showNotification_Error(t("config_page.theme.image.read_failed"));
        } finally {
            setIsReading(false);
        }
    };

    return (
        <>
            <ThemeRow label={t("config_page.theme.image.label")} desc={t("config_page.theme.image.desc")}>
                {data_url
                    ? <span className={styles.image_thumb} style={{ backgroundImage: `url("${data_url}")` }} />
                    : <p className={styles.status_text}>{t("config_page.theme.image.none")}</p>
                }
                <button type="button" className={styles.button} onClick={() => input_ref.current?.click()} disabled={is_reading}>
                    {data_url ? t("config_page.theme.image.change") : t("config_page.theme.image.choose")}
                </button>
                <input ref={input_ref} type="file" accept={THEME_IMAGE_ACCEPT} className={styles.file_input} onChange={onFileChange} />
            </ThemeRow>
            <SliderContainer
                label={t("config_page.theme.image.strength")}
                variable={image.strength}
                setterFunction={(value) => setImage({ strength: value })}
                setter_timing="on_change"
                min={0}
                max={100}
                step={1}
                show_label_values={PERCENT_MARKS}
                marks_step={25}
                valueLabelFormat="value %"
            />
            <SliderContainer
                label={t("config_page.theme.image.blur")}
                variable={image.blur}
                setterFunction={(value) => setImage({ blur: value })}
                setter_timing="on_change"
                min={0}
                max={MAX_IMAGE_BLUR}
                step={1}
                show_label_values={[0, 5, 10, 15, 20]}
                marks_step={5}
            />
        </>
    );
};

const DetailsRows = ({ theme, update }) => {
    const { t } = useI18n();
    const [is_open, setIsOpen] = useState(false);
    const { generated } = useMemo(() => buildPalette(theme.base, theme.overrides), [theme.base, theme.overrides]);
    const has_overrides = Object.keys(theme.overrides).length > 0;

    const labelOf = (name) => {
        const named = {
            dark_basic_text_color: t("config_page.theme.base_colors.text"),
            error_bc_color: t("config_page.theme.details.error"),
            error_bc_active_color: t("config_page.theme.details.error_active"),
            warning_color: t("config_page.theme.details.warning_text"),
            warning_bc_color: t("config_page.theme.details.warning"),
        };
        return named[name] ?? name.split("_")[1];
    };

    const groups = [
        { id: "accent", names: ACCENT_VARIABLES },
        { id: "neutral", names: NEUTRAL_VARIABLES, desc: t("config_page.theme.details.neutral_desc") },
        { id: "status", names: STATUS_VARIABLES },
    ];

    const setOverride = (name, hex) => update((current) => ({ ...current, overrides: { ...current.overrides, [name]: hex } }));
    const removeOverride = (name) => update((current) => {
        const overrides = { ...current.overrides };
        delete overrides[name];
        return { ...current, overrides };
    });

    return (
        <>
            <ThemeRow label={t("config_page.theme.details.label")} desc={t("config_page.theme.details.desc")}>
                {has_overrides && (
                    <button type="button" className={styles.button_secondary} onClick={() => update((current) => ({ ...current, overrides: {} }))}>
                        {t("config_page.theme.details.reset_all")}
                    </button>
                )}
                <button type="button" className={styles.button_secondary} onClick={() => setIsOpen((open) => !open)} aria-expanded={is_open}>
                    {is_open ? t("config_page.theme.details.hide") : t("config_page.theme.details.show")}
                </button>
            </ThemeRow>
            {is_open && (
                <div className={styles.details}>
                    {groups.map((group) => (
                        <div key={group.id} className={styles.detail_group}>
                            <p className={styles.detail_group_label}>{t(`config_page.theme.details.${group.id}`)}</p>
                            {group.desc && <p className={styles.detail_group_desc}>{group.desc}</p>}
                            <div className={styles.detail_grid}>
                                {group.names.map((name) => (
                                    <ThemeColorPicker
                                        key={name}
                                        label={labelOf(name)}
                                        color={theme.overrides[name] ?? generated[name]}
                                        allow_alpha={true}
                                        is_overridden={name in theme.overrides}
                                        onChange={(hex) => setOverride(name, hex)}
                                        onReset={() => removeOverride(name)}
                                    />
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </>
    );
};

const ShareRow = ({ theme }) => {
    const { t } = useI18n();
    const { is_copied, copyToClipboard } = useCopyToClipboard({ duration: 2000 });

    return (
        <ThemeRow label={t("config_page.theme.share.label")} desc={t("config_page.theme.share.desc")}>
            <button type="button" className={styles.button} onClick={() => copyToClipboard(encodeShareCode(theme))}>
                {is_copied ? t("config_page.theme.share.copied") : t("config_page.theme.share.copy")}
            </button>
        </ThemeRow>
    );
};

const DeleteRow = ({ theme }) => {
    const { t } = useI18n();
    const { deleteCustomTheme } = useUiTheme();
    const [is_confirming, setIsConfirming] = useState(false);

    // 押し間違いで消えないよう 2 回押しで消す。しばらく押さなければ元に戻る。
    useEffect(() => {
        if (!is_confirming) return;
        const timer = setTimeout(() => setIsConfirming(false), 4000);
        return () => clearTimeout(timer);
    }, [is_confirming]);

    return (
        <ThemeRow label={t("config_page.theme.delete.label")} desc={t("config_page.theme.delete.desc")}>
            <button
                type="button"
                className={clsx(styles.button_danger, { [styles.is_confirming]: is_confirming })}
                onClick={() => (is_confirming ? deleteCustomTheme(theme.id) : setIsConfirming(true))}
            >
                {is_confirming ? t("config_page.theme.delete.confirm") : t("config_page.theme.delete.button")}
            </button>
        </ThemeRow>
    );
};
