import { App } from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { describe, expect, it } from "vitest";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";
import { PipelineStack } from "../lib/pipeline-stack";

function build(dailyLimits?: boolean) {
  const app = new App();
  const env = { account: "123456789012", region: "us-east-1" };
  const data = new DataStack(app, "D", { env, frontendUrl: "http://localhost:5173" });
  const auth = new AuthStack(app, "A", { env, domainPrefix: "x", frontendUrl: "http://localhost:5173" });
  const api = new ApiStack(app, "Api", {
    env,
    table: data.table,
    dataKey: data.key,
    docsBucket: data.docsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.client,
    frontendUrl: "http://localhost:5173",
    bedrockModelId: "us.m",
    dailyLimits,
  });
  const pipeline = new PipelineStack(app, "P", {
    env,
    table: data.table,
    docsBucket: data.docsBucket,
    dataKey: data.key,
    bedrockModelId: "m",
    userPool: auth.userPool,
    appUrl: "http://localhost:5173",
    dailyLimits,
  });
  return [Template.fromStack(api), Template.fromStack(pipeline)];
}

const switchedOff = (template: Template) =>
  Object.values(template.findResources("AWS::Lambda::Function")).filter(
    (fn) => fn.Properties.Environment?.Variables?.DAILY_LIMITS === "off",
  ).length;

describe("daily upload limits switch (-c dailyLimits=off, testing only)", () => {
  it("is on by default: no function is told to skip the limits", () => {
    for (const template of build()) expect(switchedOff(template)).toBe(0);
  });

  it("when switched off, tells exactly the upload API and the extract Lambda", () => {
    const [api, pipeline] = build(false);

    expect(switchedOff(api)).toBe(1); // documents (not /me)
    expect(switchedOff(pipeline)).toBe(1); // extract (not reminders)
  });
});
