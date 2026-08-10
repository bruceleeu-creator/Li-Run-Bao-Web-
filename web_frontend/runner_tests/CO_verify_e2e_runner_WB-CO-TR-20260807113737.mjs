import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readdirSync, realpathSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runner = join(
  frontendDir,
  "e2e",
  "CO_run_playwright_WB-CO-TR-20260807113737.mjs",
);
const cli = join(frontendDir, "node_modules", "@playwright", "test", "cli.js");
const probe = "e2e/CO_e2e_runner_probe_WB-CO-TR-20260807113737.spec.ts";

function ownedRoots() {
  return readdirSync(realpathSync(tmpdir()))
    .filter((name) => name.startsWith("lirunbao-e2e-v3-"))
    .sort();
}

function invoke(args, overrides = {}, expectedExit) {
  const child = spawn(process.execPath, [runner, ...args], {
    cwd: frontendDir,
    env: { ...process.env, ...overrides },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  let observedRoot;
  let duringError;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    output += chunk;
    if (!observedRoot) {
      const match = output.match(/^E2E_RUN_ROOT=(.+)$/m);
      if (match) {
        observedRoot = match[1].trim();
        try {
          assert.deepEqual(
            ownedRoots(),
            [basename(observedRoot)],
            "invocation 运行中必须恰好存在自己的一个 owner root",
          );
        } catch (error) {
          duringError = error;
        }
      }
    }
  });
  child.stderr.on("data", (chunk) => { output += chunk; });
  return new Promise((resolvePromise, rejectPromise) => {
    child.once("error", rejectPromise);
    child.once("close", (code) => {
      try {
        if (duringError) throw duringError;
        assert.equal(code, expectedExit, output);
        const match = output.match(/^E2E_RUN_ROOT=(.+)$/m);
        assert.ok(match, output);
        assert.equal(existsSync(match[1].trim()), false, output);
        resolvePromise({ output, root: match[1].trim() });
      } catch (error) {
        rejectPromise(error);
      }
    });
  });
}

assert.deepEqual(ownedRoots(), [], "runner 回归开始前不应存在 v3 owner root");

const direct = spawnSync(process.execPath, [cli, "test", "--list"], {
  cwd: frontendDir,
  env: Object.fromEntries(
    Object.entries(process.env).filter(([name]) => !name.startsWith("LIRUNBAO_E2E_")),
  ),
  encoding: "utf8",
});
assert.notEqual(direct.status, 0, `${direct.stdout}${direct.stderr}`);
assert.match(`${direct.stdout}${direct.stderr}`, /请通过 npm run test:e2e/);
assert.deepEqual(ownedRoots(), [], "直接加载 config 不得创建 root");

const first = await invoke(["--list"], {}, 0);
assert.match(first.output, /E2E_FORMAL_GUARD=unchanged/);
assert.match(first.output, /E2E_CLEANUP=removed:/);
const second = await invoke(["--list"], {}, 0);
assert.notEqual(first.root, second.root, "连续 invocation 必须使用不同 owner root");

const assertion = await invoke(
  [probe, "--reporter=line"],
  { LIRUNBAO_E2E_TEST_FORCE_ASSERTION_FAILURE: "1" },
  1,
);
assert.match(assertion.output, /1 failed/);
assert.match(assertion.output, /E2E_CLEANUP=removed:/);

const guard = await invoke(
  ["--list"],
  { LIRUNBAO_E2E_TEST_FORCE_GUARD_FAILURE: "1" },
  1,
);
assert.match(guard.output, /注入的 E2E 正式数据库保护失败/);
assert.match(guard.output, /E2E_CLEANUP=removed:/);

const teardown = await invoke(
  ["--list"],
  { LIRUNBAO_E2E_TEST_FORCE_CLEANUP_FAILURE: "1" },
  1,
);
assert.match(teardown.output, /E2E_CLEANUP_ERROR=注入的 teardown 异常/);
assert.match(teardown.output, /E2E_CLEANUP=fallback-removed:/);

const occupied = createServer((_request, response) => {
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end('{"status":"occupied"}');
});
await new Promise((resolvePromise, rejectPromise) => {
  occupied.once("error", rejectPromise);
  occupied.listen(8765, "127.0.0.1", resolvePromise);
});
try {
  const portFailure = await invoke([probe, "--reporter=line"], {}, 1);
  assert.match(portFailure.output, /already used|address already in use|webServer/i);
  assert.match(portFailure.output, /E2E_CLEANUP=removed:/);
} finally {
  await new Promise((resolvePromise) => occupied.close(resolvePromise));
}

assert.deepEqual(ownedRoots(), [], "所有成功/故障 invocation 结束后 owner root 必须为 0");
console.log("E2E_RUNNER_BEHAVIOR=passed:success,consecutive,assertion,guard,teardown,port");
