import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  build: {
    lib: {
      entry: resolve(root, "src/boot.ts"),
      formats: ["iife"],
      name: "TeslaSlipBoot",
      fileName: () => "boot.js",
      cssFileName: "app",
    },
    outDir: resolve(root, "../app/static"),
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: false,
  },
});
