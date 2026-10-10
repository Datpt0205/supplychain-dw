/**
 * A URL-safe handle derived from a name (Vietnamese accents stripped), for the
 * tenant and workspace forms alike; a manual edit takes over. The API
 * validates and enforces uniqueness regardless.
 */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/đ/g, "d")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
