import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    // CDK synthesis (asset hashing, template generation) is slower than typical unit tests.
    testTimeout: 30_000,
  },
});
