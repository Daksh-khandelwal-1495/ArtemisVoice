import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev: Artemis API runs on :8001 (Studio owns :8000); override with ARTEMIS_PROXY_TARGET.
// Docker nginx keeps backend:8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: process.env.ARTEMIS_PROXY_TARGET ?? "http://localhost:8001", changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") } } },
});
