import type { Metadata } from "next";
import { connection } from "next/server";
import { Be_Vietnam_Pro, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { THEME_CSS_VAR_CLASS, ThemeProvider } from "@dw/ui";
import { AppFrame } from "../components/app-frame";
import { AuthProvider } from "../lib/auth/auth-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "Digital Worker Platform",
  description:
    "Không gian làm việc của worker: duyệt, tri thức, bộ nhớ và nhật ký kiểm toán",
};

// Not a variable font on Google Fonts, so each weight in use is listed: antd's
// normal and strong (400, 600) and the app's medium and bold (500, 700).
const beVietnamPro = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});
// Codes and identifiers (PO numbers, mã đề xuất, request ids): the handoff's
// monospace, read by the theme as `fontFamilyCode`.
const jetBrainsMono = JetBrains_Mono({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500"],
  display: "swap",
});

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  // Every page renders per request: the CSP nonce (middleware.ts) is minted
  // per request, and a page prerendered at build time would carry none, so
  // its scripts would be refused.
  await connection();
  return (
    // The theme's variable class on <html>: see THEME_CSS_VAR_CLASS.
    <html lang="vi" className={THEME_CSS_VAR_CLASS}>
      <body className="min-h-screen bg-background text-foreground antialiased">
        {/* `layer` puts antd's styles in `@layer antd`, which globals.css
            orders between Tailwind's base and its utilities. */}
        <AntdRegistry layer>
          <ThemeProvider
            fontFamily={beVietnamPro.style.fontFamily}
            codeFontFamily={jetBrainsMono.style.fontFamily}
          >
            <AuthProvider>
              <AppFrame>{children}</AppFrame>
            </AuthProvider>
          </ThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
