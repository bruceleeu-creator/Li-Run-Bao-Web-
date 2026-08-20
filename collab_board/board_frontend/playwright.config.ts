import { defineConfig, devices } from "@playwright/test";

// 板端 e2e：本地便携 PG（54329）固定测试库 board_e2e（一次性手工创建）；
// 每轮启动时由 board_e2e_server.py 清表保证干净状态。
// 运行：cd collab_board/board_frontend && npx playwright test
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174", viewport: { width: 1280, height: 800 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        process.platform === "win32"
          ? ".venv\\Scripts\\python collab_board\\board_e2e_server.py"
          : ".venv/bin/python collab_board/board_e2e_server.py",
      url: "http://127.0.0.1:8090/api/health",
      reuseExistingServer: false,
      cwd: "../..",
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
    },
  ],
});
