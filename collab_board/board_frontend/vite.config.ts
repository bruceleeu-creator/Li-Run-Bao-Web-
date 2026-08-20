import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",  // Playwright 探活 127.0.0.1；默认 localhost 可能仅绑 IPv6
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8090", changeOrigin: false },
    },
  },
});
