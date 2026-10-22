import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("app.css", () => {
  const css = readFileSync(resolve(process.cwd(), "src/styles/app.css"), "utf8");

  it("keeps the accent colors and motion preferences", () => {
    expect(css).toContain("#007aff");
    expect(css).toContain("#0a84ff");
    expect(css).toContain("prefers-reduced-motion");
    expect(css).toContain("scale(0.97)");
    expect(css).toContain('html[data-hide~="samples"]');
  });
});
