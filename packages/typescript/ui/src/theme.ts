import { theme, type MappingAlgorithm, type ThemeConfig } from "antd";

/**
 * The one owner of colour, type, radius and the header's size (CLAUDE.md
 * "Web UI"). Tailwind reads these through the CSS variables antd emits;
 * nothing restates a value.
 *
 * The E-HSDT v3 visual language (docs/design/ehsdt/design_handoff_ehsdt_v3,
 * kept out of the repository; its `[data-ap="light"]` and `[data-ap="dark"]`
 * variables and the `#catalog` token table), adapted where a value fails
 * `.claude/rules/ui-quality.md` §12 as rendered here; the line says so, and
 * `apps/web/components/__tests__/theme.test.ts` measures every pair. The
 * prototype's #0071e3 is 4.31:1 as text on the light page background, so
 * light primary is the nearest blue that passes, #006edc (as in the
 * dw-proterial port of the same language). Its light error #e0352b is 4.46:1
 * on white; the shell CSS's #c4271e is taken.
 */

/**
 * The class antd declares its CSS variables on (`.css-var-dw { --ant-… }`).
 * antd puts it on each of its own components; the root layout also puts it on
 * `<html>`, so plain elements and portals that are not antd's (a Radix dialog,
 * a toast) read the same tokens.
 */
export const THEME_CSS_VAR_CLASS = "css-var-dw";

/** Height of the top navbar. The header's 1px bottom rule sits inside it. */
const HEADER_HEIGHT = 56;

type Tokens = NonNullable<ThemeConfig["token"]>;

const light = {
  colorPrimary: "#006edc",
  colorLink: "#0060c0",
  colorSuccess: "#1f9d4c",
  colorWarning: "#c26a00",
  colorError: "#c4271e",
  colorInfo: "#006edc",
  colorText: "#1d1d1f",
  colorTextSecondary: "#515154",
  colorTextTertiary: "#6e6e73",
  colorBgLayout: "#f5f5f7",
  colorBgContainer: "#ffffff",
  colorBorderSecondary: "#e3e3e8",
  // A field's border shows where to type: the prototype's #8e8e93 is 2.99:1
  // on the page background, so the nearest grey that reaches 3:1.
  colorBorder: "#8c8c91",
  colorSuccessText: "#146c33",
  colorWarningText: "#9a5200",
  colorErrorText: "#c4271e",
  colorInfoText: "#0060c0",
  // Hover and press go darker, never lighter: white text stays above 4.5:1.
  colorPrimaryHover: "#0064c8",
  colorPrimaryActive: "#0058b0",
  // The prototype's selection tint (#ebf4fd) carries primary text at 4.4:1;
  // the nearest lighter tint passes.
  colorPrimaryBg: "#f0f7ff",
  colorPrimaryBgHover: "#e6f1fc",
  colorPrimaryText: "#0060c0",
  colorPrimaryBorder: "#2f86e4",
  colorFill: "rgba(120, 120, 128, 0.16)",
  colorFillSecondary: "rgba(120, 120, 128, 0.08)",
  colorFillTertiary: "rgba(120, 120, 128, 0.05)",
  boxShadowTertiary:
    "0 1px 1px rgba(0, 0, 0, 0.03), 0 8px 24px rgba(0, 0, 0, 0.05)",
} satisfies Tokens;

const dark = {
  colorPrimary: "#0071e3",
  colorLink: "#4da3ff",
  colorSuccess: "#30d158",
  colorWarning: "#ff9f0a",
  colorError: "#d63a30",
  colorInfo: "#0071e3",
  colorText: "#f5f5f7",
  colorTextSecondary: "#c7c7cc",
  colorTextTertiary: "#8e8e93",
  colorBgLayout: "#000000",
  colorBgContainer: "#1c1c1e",
  colorBorderSecondary: "#3a3a3c",
  colorBorder: "#8e8e93",
  colorSuccessText: "#30d158",
  colorWarningText: "#ff9f0a",
  // The prototype's dark error #ff453a is 3.41:1 under white text; the
  // shell's #d63a30 fills, and its text is split out.
  colorErrorText: "#ff6b61",
  colorInfoText: "#4da3ff",
  colorFill: "rgba(120, 120, 128, 0.3)",
  colorFillSecondary: "rgba(120, 120, 128, 0.18)",
  colorFillTertiary: "rgba(120, 120, 128, 0.12)",
  boxShadowTertiary: "0 0 0 0.5px rgba(255, 255, 255, 0.06)",
} satisfies Tokens;

export type ColorMode = "light" | "dark";

/** The palette for a mode: what the theme test measures. */
export const PALETTE: Record<ColorMode, Tokens> = { light, dark };

/**
 * The status tag tones, from the prototype's label table (`V3Tag`'s TONES):
 * a tint, the text drawn on it, and a border only where the tone has one.
 * `unk` (unknown: nothing decided it) is dashed purple and means nothing
 * else. Each has a light and a dark value; `theme.test.ts` measures every
 * text-on-tint pair, a translucent tint composited on both surfaces first.
 */
export type StatusTone =
  | "ok"
  | "err"
  | "warn"
  | "gold"
  | "pri"
  | "geek"
  | "unk"
  | "gray"
  | "grayStrong"
  | "outline";

export interface ToneColours {
  bg: string;
  fg: string;
  border: string | null;
}

export const STATUS_TONES: Record<
  ColorMode,
  Record<StatusTone, ToneColours>
> = {
  light: {
    ok: { bg: "#e3f7e8", fg: light.colorSuccessText, border: null },
    err: { bg: "#ffebea", fg: light.colorErrorText, border: null },
    warn: { bg: "#fff0d8", fg: light.colorWarningText, border: null },
    gold: { bg: "#fff5cc", fg: "#855c00", border: null },
    pri: { bg: "#e6f1fc", fg: light.colorInfoText, border: null },
    geek: { bg: "#eaeefd", fg: "#1d39c4", border: null },
    unk: { bg: "#f7eefc", fg: "#7b36b3", border: "#7b36b3" },
    gray: { bg: light.colorFill, fg: light.colorTextSecondary, border: null },
    grayStrong: { bg: light.colorFill, fg: light.colorText, border: null },
    outline: {
      bg: "transparent",
      fg: light.colorTextSecondary,
      border: light.colorBorder,
    },
  },
  dark: {
    ok: { bg: "rgba(48, 209, 88, 0.18)", fg: "#30d158", border: null },
    err: { bg: "rgba(255, 69, 58, 0.2)", fg: "#ff6b61", border: null },
    warn: { bg: "rgba(255, 159, 10, 0.18)", fg: "#ff9f0a", border: null },
    gold: { bg: "rgba(255, 214, 10, 0.16)", fg: "#ffd60a", border: null },
    pri: { bg: "rgba(10, 132, 255, 0.2)", fg: "#4da3ff", border: null },
    geek: { bg: "rgba(89, 126, 247, 0.2)", fg: "#85a5ff", border: null },
    unk: { bg: "rgba(191, 90, 242, 0.18)", fg: "#d18cf7", border: "#d18cf7" },
    gray: { bg: dark.colorFill, fg: dark.colorTextSecondary, border: null },
    grayStrong: { bg: dark.colorFill, fg: dark.colorText, border: null },
    outline: {
      bg: "transparent",
      fg: dark.colorTextSecondary,
      border: dark.colorBorder,
    },
  },
};

/** The tag's shape: a pill for a status, a soft square for a code. */
export const STATUS_TAG_SHAPE = {
  radius: 999,
  codeRadius: 6,
  weight: 600,
  codeWeight: 500,
  paddingInline: 8,
} as const;

/**
 * These five are antd *seed* tokens. antd drops a seed key from the token
 * override and keeps what the algorithm derived from it, which in light is
 * the seed itself and in dark is not: the dark algorithm turns #0071e3 into
 * #0363c4 (antd 6.6.5, theme/util/alias.js). Running after the base
 * algorithm, this puts the seeds back, so both modes render what the palette
 * says. The colours derived around them (hover, border, background) stay
 * antd's unless the palette names them.
 */
const keepSeedColors: MappingAlgorithm = (seed, map) => ({
  ...map!,
  colorPrimary: seed.colorPrimary,
  colorLink: seed.colorLink,
  colorSuccess: seed.colorSuccess,
  colorWarning: seed.colorWarning,
  colorError: seed.colorError,
});

/**
 * @param fontFamily the family the app loaded (next/font renames it, so the
 *   app passes what it got); antd's own system stack follows it.
 * @param codeFontFamily the same, for codes and identifiers (JetBrains Mono).
 */
export function buildTheme(
  mode: ColorMode,
  fontFamily: string,
  codeFontFamily?: string,
): ThemeConfig {
  const palette = PALETTE[mode];
  return {
    algorithm: [
      mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
      keepSeedColors,
    ],
    cssVar: { key: THEME_CSS_VAR_CLASS },
    token: {
      ...palette,
      // antd's description text (`Typography type="secondary"`, help and
      // empty texts) reads the tertiary step: 4.66:1 on the light page,
      // 5.22:1 on the dark card.
      colorTextDescription: palette.colorTextTertiary,
      // The catalog: controls 8, cards 12, dialogs 16; buttons are pills.
      borderRadius: 8,
      borderRadiusLG: 12,
      borderRadiusSM: 6,
      controlHeight: 32,
      // No text under 12px (handoff rule): antd's small size is the floor.
      fontSize: 14,
      fontSizeSM: 12,
      fontFamily: `${fontFamily}, ${theme.defaultSeed.fontFamily}`,
      fontFamilyCode: `${codeFontFamily ? `${codeFontFamily}, ` : ""}ui-monospace, SFMono-Regular, Consolas, monospace`,
    },
    components: {
      Layout: {
        headerHeight: HEADER_HEIGHT,
        headerBg: palette.colorBgContainer,
        bodyBg: palette.colorBgLayout,
      },
      Menu: {
        // The selected item's bar lands on the header's bottom rule.
        horizontalLineHeight: `${HEADER_HEIGHT - 1}px`,
        // No rule down the side of the menu in the navigation drawer.
        activeBarBorderWidth: 0,
        // antd colours the current item with colorPrimary. In the drawer and
        // the "…" popup it sits on colorPrimaryBg; the link colour reads
        // there in both modes.
        itemSelectedColor: palette.colorLink,
        // On the dark header colorPrimary, current or hovered, is 3.62:1 and
        // the link colour 6.48:1. On the light header it passes and stays.
        ...(mode === "dark" && {
          horizontalItemSelectedColor: palette.colorLink,
          horizontalItemHoverColor: palette.colorLink,
        }),
      },
      // Buttons are pills in the handoff (`--btnR: 999px`).
      Button: {
        borderRadius: 999,
        borderRadiusLG: 999,
        borderRadiusSM: 999,
        fontWeight: 500,
        primaryShadow: "none",
        defaultShadow: "none",
        dangerShadow: "none",
      },
      Card: {
        headerFontSize: 14,
        headerFontSizeSM: 14,
      },
      Table: {
        headerBg: palette.colorBgContainer,
        headerColor: palette.colorTextSecondary,
        headerSplitColor: "transparent",
        cellPaddingBlock: 10,
        cellPaddingInline: 12,
        footerBg: palette.colorBgContainer,
        footerColor: palette.colorTextSecondary,
      },
      Segmented: {
        itemSelectedBg: palette.colorBgContainer,
        itemColor: palette.colorText,
        itemHoverColor: palette.colorText,
        trackPadding: 2,
      },
      Breadcrumb: {
        itemColor: palette.colorTextTertiary,
        linkColor: palette.colorTextTertiary,
        lastItemColor: palette.colorTextSecondary,
        separatorColor: palette.colorTextTertiary,
      },
      Modal: { borderRadiusLG: 16 },
      Alert: { borderRadiusLG: 12 },
    },
  };
}
