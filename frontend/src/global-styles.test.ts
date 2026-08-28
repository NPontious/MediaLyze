import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const globalStyles = readFileSync(resolve(process.cwd(), "globals.css"), "utf8");
const componentStyles = readFileSync(resolve(process.cwd(), "src/medialyze.css"), "utf8");

describe("global theme styles", () => {
  it("themes selects without resetting their custom background layers", () => {
    const sharedControlRules = Array.from(
      globalStyles.matchAll(/input,\s*select,\s*textarea\s*\{([^}]*)\}/g),
      (match) => match[1],
    );

    expect(sharedControlRules).toHaveLength(2);
    sharedControlRules.forEach((declarations) => {
      expect(declarations).toMatch(/background-color\s*:/);
      expect(declarations).not.toMatch(/(?:^|;)\s*background\s*:/);
    });
  });

  it("uses theme-aware surfaces for nested compatibility sections", () => {
    expect(globalStyles).toMatch(/--nested-surface:\s*rgba\(255, 255, 255, 0\.36\)/);
    expect(globalStyles).toMatch(/--nested-surface:\s*rgba\(38, 35, 31, 0\.64\)/);
    expect(componentStyles).toMatch(
      /\.compatibility-capability-section\s*\{[^}]*background-color:\s*var\(--nested-surface\)/s,
    );
    expect(componentStyles).toMatch(
      /\.compatibility-video-capability\s*\{[^}]*background-color:\s*var\(--nested-surface-muted\)/s,
    );
    expect(componentStyles).not.toMatch(
      /\.compatibility-(?:capability-section|video-capability)\s*\{[^}]*background:\s*rgba\(255, 255, 255/s,
    );
  });
});
