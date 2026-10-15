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

// Everything lives in us-east-1 (SES inbound email is only offered in a few regions).
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-1" };

// Two copies of the app: production ("OneBill-*") and staging ("OneBill-Staging-*", with
// `-c stage=staging`), where new features are tested before they reach real users.
const stage: string = app.node.tryGetContext("stage") ?? "prod";
if (stage !== "prod" && stage !== "staging") throw new Error(`Unknown stage "${stage}": use prod or staging.`);
const staging = stage === "staging";
const prefix = staging ? "OneBill-Staging" : "OneBill";

// Where the app runs: the public site and local dev. Sign-in redirects, API CORS, and upload
// CORS allow each; emails link to the public site.
const devUrl: string = app.node.getContext("devUrl");
const publicUrl: string | undefined =
  app.node.tryGetContext(staging ? "stagingUrl" : "publicUrl") || undefined;
// Other addresses that also serve production (e.g. CloudFront behind a nicer main address).
const otherUrls: string[] = staging ? [] : (app.node.tryGetContext("otherUrls") ?? []);
const frontendUrls = [...(publicUrl ? [publicUrl] : []), ...otherUrls, devUrl];
const appUrl = publicUrl ?? devUrl;

// Testing only: `cdk deploy -c dailyLimits=off` skips the daily upload limits. Any deploy
// without the flag (including CI) turns them back on.
const dailyLimits = app.node.tryGetContext("dailyLimits") !== "off";
// Photo tiles next to the OCR text: "photoTiles" in cdk.json, so it's reviewed in a PR.
const photoTiles = app.node.tryGetContext("photoTiles") === "on";

const data = new DataStack(app, `${prefix}-Data`, { env, frontendUrls });

const auth = new AuthStack(app, `${prefix}-Auth`, {
  env,
  // Sign-in domains are unique across AWS; staging gets its own.
  domainPrefix: `${app.node.getContext("authDomainPrefix")}${staging ? "-staging" : ""}`,
  frontendUrls,
  // Google only allows the redirect URLs registered for it, which are production's.
  googleClientId: staging ? undefined : app.node.tryGetContext("googleClientId"),
});

new ApiStack(app, `${prefix}-Api`, {
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
  // A cost cap: staging only needs a few test questions.
  monthlyQuestions: staging ? 30 : 100,
});

new PipelineStack(app, `${prefix}-Pipeline`, {
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

// The public website. `make deploy-web` builds the frontend first; without a build,
// the stack still synthesizes (bucket and distribution, nothing uploaded).
const siteDir = join(__dirname, "..", "..", "frontend", "dist");
new WebStack(app, `${prefix}-Web`, {
  env,
  siteDir: existsSync(join(siteDir, "index.html")) ? siteDir : undefined,
});

// Account-level stack, deployed once from a laptop:
//   npx cdk deploy OneBill-Account -c budgetEmail=you@example.com
// Only synthesized when the email is supplied, so it never needs to be committed or set in CI.
const budgetEmail: string | undefined = app.node.tryGetContext("budgetEmail");
if (budgetEmail && !staging) {
  new AccountStack(app, "OneBill-Account", {
    env,
    budgetEmail,
    githubSubject: app.node.getContext("githubSubject"),
    ciRole: app.node.tryGetContext("ciRole") === "on",
  });
}
