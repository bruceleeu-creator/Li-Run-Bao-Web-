import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const formalDb = resolve(frontendDir, "..", "web_backend", "workspaces", "app.db");
const formalPaths = [formalDb, `${formalDb}-wal`, `${formalDb}-shm`];
const runPrefix = "lirunbao-e2e-v3-";

function fingerprint(path) {
  if (!existsSync(path)) return { path, exists: false };
  const content = readFileSync(path);
  return {
    path,
    exists: true,
    size: statSync(path).size,
    sha256: createHash("sha256").update(content).digest("hex"),
  };
}

function assertSafeRunRoot(runRoot) {
  if (
    realpathSync(dirname(runRoot)) !== realpathSync(tmpdir()) ||
    !basename(runRoot).startsWith(runPrefix)
  ) {
    throw new Error(`拒绝清理不可识别的 E2E run root：${runRoot}`);
  }
}

function removeRunRoot(runRoot, injectFailure) {
  assertSafeRunRoot(runRoot);
  if (injectFailure) throw new Error("注入的 teardown 异常");
  rmSync(runRoot, { recursive: true, force: true });
}

function runPlaywright(env) {
  const cli = join(frontendDir, "node_modules", "@playwright", "test", "cli.js");
  const child = spawn(process.execPath, [cli, "test", ...process.argv.slice(2)], {
    cwd: frontendDir,
    env,
    stdio: "inherit",
  });
  let interruptedSignal;
  const forward = (signal) => {
    interruptedSignal = signal;
    if (!child.killed) child.kill(signal);
  };
  process.once("SIGINT", forward);
  process.once("SIGTERM", forward);
  return new Promise((resolvePromise, rejectPromise) => {
    child.once("error", rejectPromise);
    child.once("exit", (code, signal) => {
      process.off("SIGINT", forward);
      process.off("SIGTERM", forward);
      resolvePromise({ code: code ?? 1, signal: signal || interruptedSignal });
    });
  });
}

let runRoot;
let exitCode = 1;
let failure;
try {
  const nonce = randomUUID();
  runRoot = mkdtempSync(join(tmpdir(), `${runPrefix}${nonce}-`));
  const workspace = join(runRoot, "workspaces");
  mkdirSync(workspace);
  const baseline = formalPaths.map(fingerprint);
  writeFileSync(
    join(runRoot, "owner.json"),
    JSON.stringify({ pid: process.pid, nonce, created_at: new Date().toISOString() }),
  );
  writeFileSync(join(runRoot, "formal-db-baseline.json"), JSON.stringify(baseline));
  const env = {
    ...process.env,
    LIRUNBAO_E2E_RUN_ROOT: runRoot,
    LIRUNBAO_E2E_RUN_NONCE: nonce,
    LIRUNBAO_DB_PATH: join(runRoot, "app.db"),
    LIRUNBAO_WORKSPACE_PATH: workspace,
    LIRUNBAO_AI_CONFIG_PATH: join(runRoot, ".ai_config.json"),
  };
  console.log(`E2E_RUN_ROOT=${runRoot}`);
  console.log(`E2E_RUN_NONCE=${nonce}`);

  const childResult = await runPlaywright(env);
  exitCode = childResult.signal ? 1 : childResult.code;
  const after = formalPaths.map(fingerprint);
  if (process.env.LIRUNBAO_E2E_TEST_FORCE_GUARD_FAILURE === "1") {
    throw new Error("注入的 E2E 正式数据库保护失败");
  }
  if (JSON.stringify(after) !== JSON.stringify(baseline)) {
    throw new Error(
      `E2E 正式数据库保护失败：app.db/WAL/SHM 指纹发生变化\n前：${JSON.stringify(baseline)}\n后：${JSON.stringify(after)}`,
    );
  }
  console.log("E2E_FORMAL_GUARD=unchanged");
} catch (error) {
  failure = error instanceof Error ? error : new Error(String(error));
  exitCode = 1;
  console.error(`E2E_RUNNER_ERROR=${failure.message}`);
} finally {
  if (runRoot) {
    try {
      removeRunRoot(
        runRoot,
        process.env.LIRUNBAO_E2E_TEST_FORCE_CLEANUP_FAILURE === "1",
      );
      console.log(`E2E_CLEANUP=removed:${runRoot}`);
    } catch (error) {
      const firstError = error instanceof Error ? error : new Error(String(error));
      console.error(`E2E_CLEANUP_ERROR=${firstError.message}`);
      exitCode = 1;
      try {
        removeRunRoot(runRoot, false);
        console.log(`E2E_CLEANUP=fallback-removed:${runRoot}`);
      } catch (fallbackError) {
        const message = fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
        console.error(`E2E_CLEANUP_FATAL=${runRoot}:${message}`);
      }
    }
  }
}

process.exitCode = exitCode;
