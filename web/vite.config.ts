/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, createReadStream, existsSync, statSync } from "node:fs";
import { dirname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pdfjsRoot = resolve(here, "node_modules/pdfjs-dist");
// pdf.js needs these at runtime for some PDFs (CJK CMaps, the 14 standard fonts, JPX/ICC decoders).
// They are served from /pdfjs/* (self-hosted, never from a CDN).
const PDFJS_DIRS = ["cmaps", "standard_fonts", "wasm", "iccs"];

function pdfjsAssets(): Plugin {
  let outDir = "dist";
  return {
    name: "mortise-pdfjs-assets",
    configResolved(cfg) {
      outDir = resolve(cfg.root, cfg.build.outDir);
    },
    configureServer(server) {
      server.middlewares.use("/pdfjs", (req, res, next) => {
        const rel = normalize(decodeURIComponent((req.url ?? "/").split("?")[0])).replace(/^([/\\])+/, "");
        const [dir] = rel.split(/[/\\]/);
        const file = join(pdfjsRoot, rel);
        if (!PDFJS_DIRS.includes(dir) || !file.startsWith(pdfjsRoot) || !existsSync(file) || !statSync(file).isFile()) return next();
        createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      for (const d of PDFJS_DIRS) {
        const src = join(pdfjsRoot, d);
        if (existsSync(src)) cpSync(src, join(outDir, "pdfjs", d), { recursive: true });
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), pdfjsAssets()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  preview: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
