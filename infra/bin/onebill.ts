import { existsSync } from "node:fs";
import { join } from "node:path";
import { App } from "aws-cdk-lib";
import { AccountStack } from "../lib/account-stack";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";
import { PipelineStack } from "../lib/pipeline-stack";
import { WebStack } from "../lib/web-stack";

const app = new App();

// Everything lives in us-east-1 (Phase 0 decision: SES inbound needs it later).
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-1" };
// Where the app runs: the public site ("publicUrl", once it exists) and local dev. Sign-in
// redirects, API CORS, and upload CORS allow each; emails link to the public site.
const devUrl: string = app.node.getContext("devUrl");
const publicUrl: string | undefined = app.node.tryGetContext("publicUrl") || undefined;
// Other addresses that also serve the app (e.g. CloudFront behind a nicer main address).
const otherUrls: string[] = app.node.tryGetContext("otherUrls") ?? [];
const frontendUrls = [...(publicUrl ? [publicUrl] : []), ...otherUrls, devUrl];
const appUrl = publicUrl ?? devUrl;

// Testing only: `cdk deploy -c dailyLimits=off` skips the daily upload limits. Any deploy
// without the flag (including CI) turns them back on.
const dailyLimits = app.node.tryGetContext("dailyLimits") !== "off";
// Photo tiles next to the OCR text (phase 2c): "photoTiles" in cdk.json, so it's reviewed in a PR.
const photoTiles = app.node.tryGetContext("photoTiles") === "on";

const data = new DataStack(app, "OneBill-Data", { env, frontendUrls });

const auth = new AuthStack(app, "OneBill-Auth", {
  env,
  domainPrefix: app.node.getContext("authDomainPrefix"),
  frontendUrls,
  googleClientId: app.node.tryGetContext("googleClientId"),
});

new ApiStack(app, "OneBill-Api", {
  env,
  table: data.table,
  docsBucket: data.docsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.client,
  frontendUrls,
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
  appUrl,
  enableTracing: app.node.tryGetContext("tracing") === "true",
  dailyLimits,
  photoTiles,
  gmailPasswordParameter: app.node.tryGetContext("gmailPasswordParameter") || undefined,
});

// The public website (phase 2f). `make deploy-web` builds the frontend first; without a build,
// the stack still synthesizes (bucket and distribution, nothing uploaded).
const siteDir = join(__dirname, "..", "..", "frontend", "dist");
new WebStack(app, "OneBill-Web", {
  env,
  siteDir: existsSync(join(siteDir, "index.html")) ? siteDir : undefined,
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
