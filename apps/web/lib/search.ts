/**
 * The one place a list's text search is decided (ui-quality §8): "ha noi"
 * finds "Hà Nội" and "d" finds "đ". Lists here are sorted by the server and
 * never re-sorted in the browser, so there is no client sort to own.
 */

/** `text` without its marks, "đ" as "d", in lower case: what a search compares. */
export function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[đĐ]/g, "d")
    .toLowerCase();
}

/**
 * Whether every word of `query` is in one of `fields`, marks and case
 * ignored. An empty query matches everything.
 */
export function matches(
  query: string,
  fields: readonly (string | null | undefined)[],
): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = fold(fields.filter(Boolean).join(" "));
  return words.every((word) => haystack.includes(word));
}
