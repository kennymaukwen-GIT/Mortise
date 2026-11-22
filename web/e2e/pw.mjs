// Playwright is pre-installed globally on the test machine (not a dependency of this package).
// ESM `import` does not search NODE_PATH, so load it through require().
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let pw;
try {
  pw = require("playwright");
} catch {
  pw = require("playwright-core");
}
export const { chromium } = pw;
