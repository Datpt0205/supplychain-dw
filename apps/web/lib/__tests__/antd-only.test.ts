import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * One component system (CLAUDE.md "Web UI"): antd. shadcn/ui, its Radix
 * primitives, its variant helper and sonner were removed on 2026-10-08, and
 * this keeps them out of the web app and of `@dw/ui` alike. ESLint guards the
 * app too, but `@dw/ui` has no lint step, and a dependency can come back in a
 * manifest without any import; this reads both.
 */

const WEB = resolve(__dirname, "../..");
const UI = resolve(WEB, "../../packages/typescript/ui");

/** Module specifiers that bring the old system back. */
const FORBIDDEN_MODULE =
  /^(sonner|radix-ui|@radix-ui\/.+|class-variance-authority|lucide-react|cmdk|vaul|tailwind-merge|clsx|@assistant-ui\/.+)$/;

/** What `@dw/ui` exported before: none of these may be imported from it. */
const REMOVED_PRIMITIVES = new Set([
  "Alert",
  "AlertDescription",
  "AlertTitle",
  "Badge",
  "badgeVariants",
  "Button",
  "buttonVariants",
  "Card",
  "CardContent",
  "CardDescription",
  "CardFooter",
  "CardHeader",
  "CardTitle",
  "Input",
  "Label",
  "Select",
  "Separator",
  "Skeleton",
  "Switch",
  "Table",
  "TableBody",
  "TableCell",
  "TableHead",
  "TableHeader",
  "TableRow",
  "Tabs",
  "TabsContent",
  "TabsList",
  "TabsTrigger",
  "Textarea",
  "cn",
]);

function sources(root: string): string[] {
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      if (name === "node_modules" || name === ".next" || name.startsWith(".")) {
        continue;
      }
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(ts|tsx|mts|js|mjs|css)$/.test(name)) found.push(path);
    }
  };
  walk(root);
  return found;
}

const IMPORT =
  /(?:import|export)\s+(?:type\s+)?([\s\S]*?)\s+from\s+["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)|require\(\s*["']([^"']+)["']\s*\)|@import\s+["']([^"']+)["']/g;

/** Every forbidden import in one source text, as "specifier" or "name from @dw/ui". */
export function forbiddenImports(text: string): string[] {
  const hits: string[] = [];
  for (const match of text.matchAll(IMPORT)) {
    const names = match[1] ?? "";
    const specifier = match[2] ?? match[3] ?? match[4] ?? match[5] ?? "";
    if (FORBIDDEN_MODULE.test(specifier)) hits.push(specifier);
    if (specifier === "@dw/ui") {
      for (const raw of names.replace(/[{}]/g, "").split(",")) {
        const name = raw
          .trim()
          .replace(/^type\s+/, "")
          .split(/\s+as\s+/)[0];
        if (name && REMOVED_PRIMITIVES.has(name))
          hits.push(`${name} from @dw/ui`);
      }
    }
  }
  return hits;
}

describe("antd is the only component system", () => {
  it("catches each way the old system could come back", () => {
    expect(forbiddenImports('import { toast } from "sonner";')).toEqual([
      "sonner",
    ]);
    expect(
      forbiddenImports('import { Slot } from "@radix-ui/react-slot";'),
    ).toEqual(["@radix-ui/react-slot"]);
    expect(forbiddenImports('import { Dialog } from "radix-ui";')).toEqual([
      "radix-ui",
    ]);
    expect(
      forbiddenImports(
        'import {\n  PageHeader,\n  Button as B,\n} from "@dw/ui";',
      ),
    ).toEqual(["Button from @dw/ui"]);
    expect(forbiddenImports('import { PageHeader } from "@dw/ui";')).toEqual(
      [],
    );
    expect(forbiddenImports('import { Button } from "antd";')).toEqual([]);
  });

  it("no source in the web app or @dw/ui imports it", () => {
    const offenders = [...sources(WEB), ...sources(UI)]
      .filter((path) => !path.endsWith("antd-only.test.ts"))
      .flatMap((path) =>
        forbiddenImports(readFileSync(path, "utf8")).map(
          (hit) => `${relative(WEB, path)}: ${hit}`,
        ),
      );
    expect(offenders).toEqual([]);
  });

  it("no manifest depends on it", () => {
    const offenders = [WEB, UI].flatMap((dir) => {
      const manifest = JSON.parse(
        readFileSync(join(dir, "package.json"), "utf8"),
      ) as Record<string, Record<string, string> | undefined>;
      return ["dependencies", "devDependencies", "peerDependencies"].flatMap(
        (field) =>
          Object.keys(manifest[field] ?? {})
            .filter((name) => FORBIDDEN_MODULE.test(name))
            .map((name) => `${relative(WEB, dir) || "apps/web"}: ${name}`),
      );
    });
    expect(offenders).toEqual([]);
  });

  it("no shadcn primitive file is left in @dw/ui", () => {
    const left = readdirSync(join(UI, "src")).filter((name) =>
      /^(alert|badge|button|card|input|label|separator|skeleton|switch|table|tabs|cn)\.tsx?$/.test(
        name,
      ),
    );
    expect(left).toEqual([]);
  });
});
