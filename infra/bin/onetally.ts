import { App } from "aws-cdk-lib";
import { AccountStack } from "../lib/account-stack";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";
import { PipelineStack } from "../lib/pipeline-stack";

const app = new App();

// Everything lives in us-east-1 (Phase 0 decision: SES inbound needs it later).
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-1" };
const frontendUrl: string = app.node.getContext("frontendUrl");

const data = new DataStack(app, "OneTally-Data", { env, frontendUrl });

const auth = new AuthStack(app, "OneTally-Auth", {
  env,
  domainPrefix: app.node.getContext("authDomainPrefix"),
  frontendUrl,
});

new ApiStack(app, "OneTally-Api", {
  env,
  table: data.table,
  dataKey: data.key,
  docsBucket: data.docsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.client,
  frontendUrl,
  // Debug switch: npx cdk deploy OneTally-Api -c tracing=true
  enableTracing: app.node.tryGetContext("tracing") === "true",
});

new PipelineStack(app, "OneTally-Pipeline", {
  env,
  table: data.table,
  docsBucket: data.docsBucket,
  dataKey: data.key,
  bedrockModelId: app.node.getContext("bedrockModelId"),
  userPool: auth.userPool,
  appUrl: frontendUrl,
  enableTracing: app.node.tryGetContext("tracing") === "true",
});

// Account-level stack, deployed once from a laptop:
//   npx cdk deploy OneTally-Account -c budgetEmail=you@example.com
// Only synthesized when the email is supplied, so it never needs to be committed or set in CI.
const budgetEmail: string | undefined = app.node.tryGetContext("budgetEmail");
if (budgetEmail) {
  new AccountStack(app, "OneTally-Account", {
    env,
    budgetEmail,
    githubSubject: app.node.getContext("githubSubject"),
  });
}
