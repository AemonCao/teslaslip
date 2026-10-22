import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: {
    outDir: resolve(root, "../app/static"),
    emptyOutDir: true,
    manifest: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        settings: resolve(root, "src/entries/settings.ts"),
        "drive-map": resolve(root, "src/entries/drive-map.ts"),
        "charge-map": resolve(root, "src/entries/charge-map.ts"),
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    cors: true,
    origin: "http://127.0.0.1:5173",
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
