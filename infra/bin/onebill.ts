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

// Testing only: `cdk deploy -c dailyLimits=off` skips the daily upload limits. Any deploy
// without the flag (including CI) turns them back on.
const dailyLimits = app.node.tryGetContext("dailyLimits") !== "off";
// Photo tiles next to the OCR text (phase 2c): "photoTiles" in cdk.json, so it's reviewed in a PR.
const photoTiles = app.node.tryGetContext("photoTiles") === "on";

const data = new DataStack(app, "OneBill-Data", { env, frontendUrl });

const auth = new AuthStack(app, "OneBill-Auth", {
  env,
  domainPrefix: app.node.getContext("authDomainPrefix"),
  frontendUrl,
  googleClientId: app.node.tryGetContext("googleClientId"),
});

new ApiStack(app, "OneBill-Api", {
  env,
  table: data.table,
  docsBucket: data.docsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.client,
  frontendUrl,
  // Debug switch: npx cdk deploy OneBill-Api -c tracing=true
  enableTracing: app.node.tryGetContext("tracing") === "true",
  dailyLimits,
  bedrockModelId: app.node.getContext("bedrockModelId"),
});

new PipelineStack(app, "OneBill-Pipeline", {
  env,
  table: data.table,
  docsBucket: data.docsBucket,
  bedrockModelId: app.node.getContext("bedrockModelId"),
  userPool: auth.userPool,
  appUrl: frontendUrl,
  enableTracing: app.node.tryGetContext("tracing") === "true",
  dailyLimits,
  photoTiles,
});

// Account-level stack, deployed once from a laptop:
//   npx cdk deploy OneBill-Account -c budgetEmail=you@example.com
// Only synthesized when the email is supplied, so it never needs to be committed or set in CI.
const budgetEmail: string | undefined = app.node.tryGetContext("budgetEmail");
if (budgetEmail) {
  new AccountStack(app, "OneBill-Account", {
    env,
    budgetEmail,
    githubSubject: app.node.getContext("githubSubject"),
    ciRole: app.node.tryGetContext("ciRole") === "on",
  });
}
