import { defineConfig } from "vite";

// The delivered design is a single self-contained Design Component
// (THERMOX.dc.html at the repository root). This config exists so the
// container build has a real entry point and so an API proxy is available for
// local development against the FastAPI backend.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
