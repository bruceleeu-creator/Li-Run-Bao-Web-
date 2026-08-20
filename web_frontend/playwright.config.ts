import { defineConfig, devices } from "@playwright/test";
import { readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

const required = [
  "LIRUNBAO_E2E_RUN_ROOT",
  "LIRUNBAO_E2E_RUN_NONCE",
  "LIRUNBAO_DB_PATH",
  "LIRUNBAO_WORKSPACE_PATH",
  "LIRUNBAO_AI_CONFIG_PATH",
] as const;
for (const name of required) {
  if (!process.env[name]) {
    throw new Error(`缺少 ${name}；请通过 npm run test:e2e 启动隔离的 Playwright invocation`);
  }
}

const runRoot = resolve(process.env.LIRUNBAO_E2E_RUN_ROOT!);
if (
  realpathSync(dirname(runRoot)) !== realpathSync(tmpdir()) ||
  !basename(runRoot).startsWith("lirunbao-e2e-v3-")
) {
  throw new Error(`拒绝读取不可识别的 E2E run root：${runRoot}`);
}
const owner = JSON.parse(readFileSync(join(runRoot, "owner.json"), "utf8")) as {
  nonce?: string;
  pid?: number;
};
if (owner.nonce !== process.env.LIRUNBAO_E2E_RUN_NONCE || typeof owner.pid !== "number") {
  throw new Error(`E2E run root owner/nonce 不匹配：${runRoot}`);
}
if (
  resolve(process.env.LIRUNBAO_DB_PATH!) !== join(runRoot, "app.db") ||
  resolve(process.env.LIRUNBAO_WORKSPACE_PATH!) !== join(runRoot, "workspaces") ||
  resolve(process.env.LIRUNBAO_AI_CONFIG_PATH!) !== join(runRoot, ".ai_config.json")
) {
  throw new Error("E2E 三个存储 override 必须属于同一个 owner run root");
}

const childEnv = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
);

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  // E2E 共用同一个本机 SQLite/session；并行文件会互相清空或替换单用户会话。
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1280, height: 800 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      // Windows 主机用 Scripts/，macOS/Linux 用 bin/（v26.1 迁移补点）
      command:
        process.platform === "win32"
          ? ".venv\\Scripts\\python -m web_backend.CO_run_WB-CO-TR-20260805160732"
          : ".venv/bin/python -m web_backend.CO_run_WB-CO-TR-20260805160732",
      url: "http://127.0.0.1:8765/api/health",
      reuseExistingServer: false,
      cwd: "..",
      env: childEnv,
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      env: childEnv,
    },
  ],
});
