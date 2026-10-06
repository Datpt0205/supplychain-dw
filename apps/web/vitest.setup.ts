/**
 * What antd asks of a browser that jsdom does not implement, given to every
 * test file once: `matchMedia` (Table's responsive columns, Grid, Segmented)
 * and `ResizeObserver` (Table, Tooltip). A file that needs a different answer
 * still defines its own; these only fill a gap.
 */
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
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
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
