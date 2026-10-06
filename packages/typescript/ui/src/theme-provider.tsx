"use client";

import {
  createContext,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { App, ConfigProvider } from "antd";
import viVN from "antd/locale/vi_VN";
import dayjs from "dayjs";
import "dayjs/locale/vi";
import { buildTheme, type ColorMode } from "./theme";

// antd's pickers format through dayjs, so its locale is set beside antd's.
dayjs.locale("vi");

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

const prefersDark = () => window.matchMedia(DARK_QUERY).matches;
// The server cannot see the OS setting: it renders light, and a dark OS
// switches on the first client render.
const onServer = () => false;

const ColorModeContext = createContext<ColorMode>("light");

/** The mode the theme is drawn in, for a piece that picks a mode's value
 * (`StatusTag` and its tones). */
export function useColorMode(): ColorMode {
  return useContext(ColorModeContext);
}

/**
 * The antd theme, in light or dark as the OS asks (design v3), with the
 * Vietnamese locale and antd's `App`, whose `App.useApp()` gives `message`,
 * `notification` and `modal` that read this theme.
 */
export function ThemeProvider({
  fontFamily,
  codeFontFamily,
  children,
}: {
  /** The family the app loaded; see `buildTheme`. */
  fontFamily: string;
  /** The monospace family the app loaded, for codes and identifiers. */
  codeFontFamily?: string;
  children: ReactNode;
}) {
  const dark = useSyncExternalStore(subscribe, prefersDark, onServer);
  const themeConfig = useMemo(
    () => buildTheme(dark ? "dark" : "light", fontFamily, codeFontFamily),
    [dark, fontFamily, codeFontFamily],
  );
  return (
    <ColorModeContext.Provider value={dark ? "dark" : "light"}>
      <ConfigProvider theme={themeConfig} locale={viVN}>
        <App>{children}</App>
      </ConfigProvider>
    </ColorModeContext.Provider>
  );
}
