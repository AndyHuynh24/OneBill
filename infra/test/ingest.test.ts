import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";

const MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0";
let template: Template;

beforeAll(() => {
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
    bedrockModelId: MODEL,
  });
  template = Template.fromStack(api);
});

type Statement = { Action: string | string[]; Resource: unknown; Condition?: Record<string, unknown> };

function ingestStatements(): Statement[] {
  const roles = template.findResources("AWS::IAM::Role");
  const roleId = Object.keys(roles).find((id) => id.startsWith("IngestFunction"));
  const policies = Object.values(template.findResources("AWS::IAM::Policy")).filter((p) =>
    JSON.stringify(p.Properties.Roles).includes(roleId!),
  );
  return policies.flatMap((p) => p.Properties.PolicyDocument.Statement as Statement[]);
}

describe("ApiStack — Gmail ingest (POST /ingest/email)", () => {
  it("runs the ingest Lambda within API Gateway's 30 s limit, with the table and model", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "api.ingest.handler.handler",
      Timeout: 29,
      Environment: { Variables: Match.objectLike({ TABLE_NAME: Match.anyValue(), BEDROCK_MODEL_ID: MODEL }) },
    });
  });

  it("routes POST /ingest/email to it without the JWT authorizer (the key is checked in code)", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "POST /ingest/email",
      AuthorizationType: "NONE",
      // API Gateway rejects scopes on a route without a JWT authorizer (first deploy failed on
      // this): the API's default "openid" scope must not be inherited here.
      AuthorizationScopes: Match.absent(),
    });
  });

  it("grants only the DynamoDB calls ingest makes: no Query, no Scan, no indexes", () => {
    const dynamo = ingestStatements().filter((s) => [s.Action].flat().some((a) => a.startsWith("dynamodb:")));
    expect(dynamo.flatMap((s) => [s.Action].flat()).sort()).toEqual([
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]);
    expect(JSON.stringify(dynamo)).not.toContain("/index/");
  });

  it("may invoke only the configured model, through its inference profile", () => {
    const bedrock = ingestStatements().filter((s) => [s.Action].flat().includes("bedrock:InvokeModel"));
    expect(bedrock).toHaveLength(2);
    const conditioned = bedrock.find((s) => s.Condition);
    expect(JSON.stringify(conditioned!.Condition)).toContain("bedrock:InferenceProfileArn");
  });

  it("uses the table's key only through DynamoDB", () => {
    const kms = ingestStatements().find((s) => [s.Action].flat().some((a) => a.startsWith("kms:")));
    expect(JSON.stringify(kms!.Condition)).toContain("dynamodb.us-east-1.amazonaws.com");
  });
});
