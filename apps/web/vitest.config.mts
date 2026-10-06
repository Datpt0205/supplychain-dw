import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the web app.
 *
 * `e2e/` is excluded on purpose: those are Playwright specs, and Vitest
 * would collect them and then hang waiting for a browser that never comes.
 */
export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        include: ["**/*.test.ts", "**/*.test.tsx"],
        exclude: ["node_modules/**", ".next/**", "e2e/**"],
        setupFiles: ["./vitest.setup.ts"],
    },
});
