import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  PALETTE,
  STATUS_TONES,
  StatusTag,
  ThemeProvider,
  buildTheme,
  type ColorMode,
} from "@dw/ui";

afterEach(cleanup);

/** "#rrggbb" or "rgba(r, g, b, a)" as [r, g, b, a]. */
function parse(colour: string): [number, number, number, number] {
  if (colour.startsWith("#")) {
    const hex = colour.slice(1);
    return [0, 2, 4]
      .map((i) => parseInt(hex.slice(i, i + 2), 16))
      .concat(1) as [number, number, number, number];
  }
  const inner = /rgba?\(([^)]+)\)/.exec(colour)![1]!.split(",").map(Number);
  return [inner[0]!, inner[1]!, inner[2]!, inner[3] ?? 1];
}

/** `colour` drawn over the opaque `back`: a translucent tint as rendered. */
function over(colour: string, back: string): string {
  const b = parse(back);
  const c = parse(colour);
  return `#${[0, 1, 2]
    .map((i) => Math.round(c[i]! * c[3] + b[i]! * (1 - c[3])))
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("")}`;
}

function luminance(colour: string): number {
  const [r, g, b] = parse(colour).map((v, i) => {
    if (i === 3) return v;
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x! + 0.05) / (y! + 0.05);
}

const MODES: ColorMode[] = ["light", "dark"];
const SURFACES = ["colorBgContainer", "colorBgLayout"] as const;

describe("theme contrast, both modes (ui-quality §12)", () => {
  const text = MODES.flatMap((mode) =>
    (
      [
        "colorText",
        "colorTextSecondary",
        "colorTextTertiary",
        "colorLink",
        "colorSuccessText",
        "colorWarningText",
        "colorErrorText",
        "colorInfoText",
      ] as const
    ).flatMap((fore) =>
      SURFACES.map((surface) => [mode, fore, surface] as const),
    ),
  );
  it.each(text)("%s: %s on %s is text, 4.5:1", (mode, fore, surface) => {
    const palette = PALETTE[mode];
    expect(
      contrast(String(palette[fore]), String(palette[surface])),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(MODES)("%s: white on the primary button is text, 4.5:1", (mode) => {
    expect(
      contrast("#ffffff", String(PALETTE[mode].colorPrimary)),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(
    MODES.flatMap((mode) =>
      SURFACES.map((surface) => [mode, surface] as const),
    ),
  )("%s: a field's border on %s is 3:1", (mode, surface) => {
    const palette = PALETTE[mode];
    expect(
      contrast(String(palette.colorBorder), String(palette[surface])),
    ).toBeGreaterThanOrEqual(3);
  });
});

describe("status tag tones, as rendered (ui-quality §12)", () => {
  // A tag's text on its own tint is the trap §12 names: measured on both
  // surfaces of both modes, a translucent tint composited onto each first.
  const pairs = MODES.flatMap((mode) =>
    Object.entries(STATUS_TONES[mode]).flatMap(([tone, colours]) =>
      SURFACES.map((surface) => [mode, tone, surface, colours] as const),
    ),
  );
  it.each(pairs)(
    "%s: the %s tag's text on %s is 4.5:1",
    (mode, _, surface, c) => {
      const back = String(PALETTE[mode][surface]);
      const tint = c.bg === "transparent" ? back : over(c.bg, back);
      expect(contrast(c.fg, tint)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(MODES)(
    "%s: unknown keeps a dashed border of its own colour, 3:1",
    (mode) => {
      const unk = STATUS_TONES[mode].unk;
      expect(unk.border).toBe(unk.fg);
      expect(
        contrast(unk.border!, String(PALETTE[mode].colorBgContainer)),
      ).toBeGreaterThanOrEqual(3);
    },
  );

  it.each(MODES)("%s: unknown's colour means nothing else", (mode) => {
    const tones = STATUS_TONES[mode];
    const others = Object.entries(tones).filter(([tone]) => tone !== "unk");
    expect(others.map(([, c]) => c.fg)).not.toContain(tones.unk.fg);
  });
});

describe("the theme's type", () => {
  it.each(MODES)(
    "%s: no text under 12px, and codes in the mono font",
    (mode) => {
      const token = buildTheme(mode, "Be Vietnam Pro", "JetBrains Mono").token!;
      expect(token.fontSizeSM).toBeGreaterThanOrEqual(12);
      expect(token.fontSize).toBeGreaterThanOrEqual(12);
      expect(token.fontFamilyCode?.startsWith("JetBrains Mono, ")).toBe(true);
    },
  );

  it("the root layout loads the mono font and hands it to the theme", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const layout = readFileSync(
      resolve(fileURLToPath(import.meta.url), "../../../app/layout.tsx"),
      "utf8",
    );
    expect(layout).toContain("JetBrains_Mono(");
    expect(layout).toContain("codeFontFamily={jetBrainsMono.style.fontFamily}");
  });
});

describe("StatusTag", () => {
  function withScheme(dark: boolean) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => ({
        matches: dark && query.includes("dark"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }

  it.each([
    [false, "light"],
    [true, "dark"],
  ] as const)("draws the %s mode's tone (%s)", (dark, mode) => {
    withScheme(dark);
    render(
      <ThemeProvider fontFamily="Be Vietnam Pro">
        <StatusTag tone="err">Trễ SLA</StatusTag>
      </ThemeProvider>,
    );
    const tag = screen.getByText("Trễ SLA");
    expect(tag.style.color).toBe(
      (() => {
        const [r, g, b] = parse(STATUS_TONES[mode].err.fg);
        return `rgb(${r}, ${g}, ${b})`;
      })(),
    );
  });

  it("dashes the unknown tone and offers its meaning to the keyboard", () => {
    withScheme(false);
    render(
      <ThemeProvider fontFamily="Be Vietnam Pro">
        <StatusTag tone="unk" tip="Mốc chưa xác nhận">
          Chưa có chính sách xác nhận
        </StatusTag>
      </ThemeProvider>,
    );
    const tag = screen.getByText("Chưa có chính sách xác nhận");
    expect(tag.style.borderStyle).toBe("dashed");
    expect(tag.getAttribute("tabindex")).toBe("0");
  });
});
