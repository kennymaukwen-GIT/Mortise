import "@testing-library/jest-dom/vitest";

// jsdom has no pointer capture; the viewer and the corner handles use it.
if (typeof HTMLElement !== "undefined" && !("hasPointerCapture" in HTMLElement.prototype)) {
  const captured = new WeakMap<Element, Set<number>>();
  const of = (el: Element) => captured.get(el) ?? (captured.set(el, new Set()), captured.get(el)!);
  Object.assign(HTMLElement.prototype, {
    setPointerCapture(this: Element, id: number) {
      of(this).add(id);
    },
    hasPointerCapture(this: Element, id: number) {
      return of(this).has(id);
    },
    releasePointerCapture(this: Element, id: number) {
      of(this).delete(id);
    },
  });
}
