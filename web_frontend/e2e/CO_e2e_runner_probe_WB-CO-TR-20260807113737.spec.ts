import { expect, test } from "@playwright/test";

test("外层 E2E runner assertion 故障注入探针", () => {
  expect(process.env.LIRUNBAO_E2E_TEST_FORCE_ASSERTION_FAILURE).not.toBe("1");
});
