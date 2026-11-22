// Lists the licence of every installed package and fails on copyleft licences (GPL/AGPL/LGPL/SSPL)
// or packages without a licence. Fonts must be OFL (or similarly permissive).
//   npm run licenses            # production dependencies (what ships in dist/)
//   npm run licenses -- --all   # every installed package, dev tools included
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const all = process.argv.includes("--all");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const BAD = /(^|[^L])GPL|AGPL|LGPL|SSPL|BUSL|Commons Clause/i;

function info(dir) {
  const p = join(dir, "package.json");
  if (!existsSync(p)) return null;
  const j = JSON.parse(readFileSync(p, "utf8"));
  let lic = j.license ?? (Array.isArray(j.licenses) ? j.licenses.map((l) => l.type ?? l).join(" OR ") : undefined);
  if (lic && typeof lic === "object") lic = lic.type;
  return { name: j.name, version: j.version, license: lic ?? "UNKNOWN", deps: Object.keys({ ...(j.dependencies ?? {}), ...(j.optionalDependencies ?? {}) }) };
}

function find(name, from) {
  let dir = from;
  for (;;) {
    const cand = join(dir, "node_modules", name);
    if (existsSync(join(cand, "package.json"))) return cand;
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

const seen = new Map();
function walk(name, from) {
  const dir = find(name, from);
  if (!dir || seen.has(dir)) return;
  const i = info(dir);
  if (!i) return;
  seen.set(dir, i);
  for (const d of i.deps) walk(d, dir);
}

if (all) {
  const nm = join(root, "node_modules");
  for (const n of readdirSync(nm)) {
    if (n.startsWith(".")) continue;
    if (n.startsWith("@")) for (const m of readdirSync(join(nm, n))) walk(`${n}/${m}`, root);
    else walk(n, root);
  }
} else {
  for (const n of Object.keys(pkg.dependencies ?? {})) walk(n, root);
}

const rows = [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
let bad = 0;
const counts = {};
for (const r of rows) {
  counts[r.license] = (counts[r.license] ?? 0) + 1;
  const flag = BAD.test(String(r.license)) || r.license === "UNKNOWN";
  if (flag) bad++;
  if (flag || !all) console.log(`${flag ? "!!" : "  "} ${r.name}@${r.version}  ${r.license}`);
}
console.log(`\n${rows.length} packages (${all ? "all installed" : "production"}):`, counts);
if (bad) {
  console.error(`${bad} package(s) need a look`);
  process.exit(1);
}
