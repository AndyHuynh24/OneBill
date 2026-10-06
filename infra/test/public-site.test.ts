import { App } from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { describe, expect, it } from "vitest";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";

// The app runs at the public site and at local dev: sign-in, the API, and uploads allow both.
const URLS = ["https://onebill.example", "http://localhost:5173"];

function stacks() {
  const app = new App({ context: { "aws:cdk:bundling-stacks": [] } });
  const env = { account: "123456789012", region: "us-east-1" };
  const data = new DataStack(app, "D", { env, frontendUrls: URLS });
  const auth = new AuthStack(app, "A", { env, domainPrefix: "x", frontendUrls: URLS });
  const api = new ApiStack(app, "Api", {
    env,
    table: data.table,
    docsBucket: data.docsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.client,
    frontendUrls: URLS,
    dailyLimits: true,
    bedrockModelId: "test-model",
  });
  return { data: Template.fromStack(data), auth: Template.fromStack(auth), api: Template.fromStack(api) };
}

describe("public site and local dev", () => {
  const t = stacks();

  it("sign-in can return to either address", () => {
    t.auth.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      CallbackURLs: ["https://onebill.example/callback", "http://localhost:5173/callback"],
      LogoutURLs: ["https://onebill.example/", "http://localhost:5173/"],
    });
  });

  it("the API accepts browser calls from either address, and no other", () => {
    const api = Object.values(t.api.findResources("AWS::ApiGatewayV2::Api"))[0];
    expect(api.Properties.CorsConfiguration.AllowOrigins).toEqual(URLS);
  });

  it("uploads to the docs bucket are allowed from either address, and no other", () => {
    const bucket = Object.values(t.data.findResources("AWS::S3::Bucket")).find((b) => b.Properties.CorsConfiguration);
    expect(bucket?.Properties.CorsConfiguration.CorsRules[0].AllowedOrigins).toEqual(URLS);
  });
});
