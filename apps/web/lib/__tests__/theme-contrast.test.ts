import { theme } from "antd";
import { describe, expect, it } from "vitest";
import {
  buildTheme,
  type ColorMode,
} from "../../../../packages/typescript/ui/src/theme";

/**
 * Text the platform draws in a status or danger colour stays readable in both
 * modes (WCAG AA, 4.5:1). The values come from the theme itself, through
 * antd's own token derivation, so a palette change is measured, not trusted.
 */

function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((at) => {
    const c = parseInt(value.slice(at, at + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

/** antd may hand a colour back as rgb()/8-digit hex; only opaque hex is compared. */
function hex(value: unknown): string {
  expect(typeof value).toBe("string");
  let text = String(value);
  if (/^#[0-9a-f]{3}$/i.test(text)) {
    text = `#${[...text.slice(1)].map((c) => c + c).join("")}`;
  }
  expect(text).toMatch(/^#[0-9a-f]{6}$/i);
  return text;
}

const MODES: ColorMode[] = ["light", "dark"];

describe.each(MODES)("the %s theme", (mode) => {
  const config = buildTheme(mode, "Be Vietnam Pro");
  const token = theme.getDesignToken(config);
  const components = config.components ?? {};
  const backgrounds = {
    card: hex(token.colorBgContainer),
    layout: hex(token.colorBgLayout),
  };

  it.each([
    "colorText",
    "colorTextSecondary",
    "colorTextTertiary",
    "colorLink",
    "colorErrorText",
    "colorSuccessText",
    "colorWarningText",
  ] as const)("%s reads on the card and the page", (name) => {
    for (const background of Object.values(backgrounds)) {
      expect(contrast(hex(token[name]), background)).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });

  it.each([
    ["colorSuccess", "colorSuccessBg"],
    ["colorWarning", "colorWarningBg"],
    ["colorError", "colorErrorBg"],
  ] as const)("a %s tag's text reads on its own tint", (text, tint) => {
    const tag = components.Tag as Record<string, string> | undefined;
    const color = tag?.[text] ?? token[text];
    expect(contrast(hex(color), hex(token[tint]))).toBeGreaterThanOrEqual(4.5);
  });

  it("a danger button's label reads, outlined on the card and filled", () => {
    const button = components.Button as Record<string, string> | undefined;
    const base = button?.colorError ?? token.colorError;
    const label = button?.dangerColor ?? token.colorTextLightSolid;
    expect(contrast(hex(base), backgrounds.card)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(hex(label), hex(base))).toBeGreaterThanOrEqual(4.5);
  });
});
