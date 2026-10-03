import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    // CDK synthesis (asset hashing, template generation) is slower than typical unit tests.
    testTimeout: 30_000,
    hookTimeout: 30_000, // beforeAll synthesizes whole stacks
    // Skip asset bundling (the Pillow layer) in unit tests: templates are the same, and synth stays
    // fast and offline. Real `cdk synth`/`deploy` still build it.
    env: { CDK_CONTEXT_JSON: JSON.stringify({ "aws:cdk:bundling-stacks": [] }) },
  },
});
