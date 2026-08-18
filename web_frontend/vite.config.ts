import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 仅本机使用；构建产物由 web_backend 静态挂载，端口由 .command 启动器管理
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // 开发时把 /api 代理到本机后端，使前端与真实 FastAPI 联动
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
