import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";

// base "./" so the built site works from any GitHub Pages path (https://owner.github.io/repo/).
export default defineConfig({
  base: "./",
  plugins: [preact()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
    globals: false,
  },
});
