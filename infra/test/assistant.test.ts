import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";
import { PipelineStack } from "../lib/pipeline-stack";

const MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0";
const URLS = ["http://localhost:5173"];
let data: Template;
let api: Template;
let pipeline: Template;

beforeAll(() => {
  const app = new App();
  const env = { account: "123456789012", region: "us-east-1" };
  const d = new DataStack(app, "D", { env, frontendUrls: URLS });
  const auth = new AuthStack(app, "A", { env, domainPrefix: "x", frontendUrls: URLS });
  const a = new ApiStack(app, "Api", {
    env,
    table: d.table,
    docsBucket: d.docsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.client,
    frontendUrls: URLS,
    bedrockModelId: MODEL,
  });
  const p = new PipelineStack(app, "P", {
    env,
    table: d.table,
    docsBucket: d.docsBucket,
    bedrockModelId: MODEL,
    userPool: auth.userPool,
    appUrl: URLS[0],
  });
  data = Template.fromStack(d);
  api = Template.fromStack(a);
  pipeline = Template.fromStack(p);
});

type Statement = { Action: string | string[]; Resource: unknown; Condition?: unknown };

function statementsFor(template: Template, handler: string): Statement[] {
  const fns = template.findResources("AWS::Lambda::Function", { Properties: { Handler: handler } });
  const roleId = Object.values(fns)[0].Properties.Role["Fn::GetAtt"][0];
  return Object.values(template.findResources("AWS::IAM::Policy"))
    .filter((p) => p.Properties.Roles.some((r: { Ref: string }) => r.Ref === roleId))
    .flatMap((p) => p.Properties.PolicyDocument.Statement);
}

const actions = (statements: Statement[], prefix: string) =>
  statements.flatMap((s) => [s.Action].flat()).filter((a) => a.startsWith(prefix)).sort();

describe("search index (S3 Vectors)", () => {
  it("lives with the assistant, not the data stack (so replacing it never locks an export)", () => {
    data.resourceCountIs("AWS::S3Vectors::Index", 0);
    pipeline.resourceCountIs("AWS::S3Vectors::Index", 0);
  });

  it("is one vector bucket with one 512-dimension cosine index", () => {
    api.resourceCountIs("AWS::S3Vectors::VectorBucket", 1);
    api.hasResourceProperties("AWS::S3Vectors::Index", {
      DataType: "float32",
      Dimension: 512,
      DistanceMetric: "cosine",
    });
  });

  it("filters only on the owner's userId; the item's bill, row, and fingerprint are just returned", () => {
    api.hasResourceProperties("AWS::S3Vectors::Index", {
      MetadataConfiguration: { NonFilterableMetadataKeys: ["docId", "row", "textHash"] },
    });
  });
});

describe("Ask OneBill (the assistant Lambda)", () => {
  const ASSISTANT = "api.assistant.handler.handler";

  it("answers POST /assistant/ask behind the sign-in check", () => {
    api.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "POST /assistant/ask",
      AuthorizationType: "JWT",
    });
  });

  it("can't write bills: it reads, and only counts questions", () => {
    const statements = statementsFor(api, ASSISTANT);
    expect(actions(statements, "dynamodb:")).toEqual([
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Query", // GSI2: a trip's receipts
      "dynamodb:UpdateItem", // the question counters only
    ]);
  });

  it("can only query the search index, never change it", () => {
    const statements = statementsFor(api, ASSISTANT);
    expect(actions(statements, "s3vectors:")).toEqual(["s3vectors:GetVectors", "s3vectors:QueryVectors"]);
  });

  it("may only write the question counters (IAM-enforced), never a bill", () => {
    const update = statementsFor(api, ASSISTANT).find((s) => [s.Action].flat().includes("dynamodb:UpdateItem"));
    expect(update?.Condition).toEqual({
      "ForAllValues:StringEquals": { "dynamodb:Attributes": ["PK", "SK", "questions", "expiresAt"] },
      StringEqualsIfExists: { "dynamodb:ReturnValues": ["NONE", "UPDATED_NEW"] },
    });
  });

  it("can call Haiku and the Titan embedding model, nothing else", () => {
    const resources = JSON.stringify(statementsFor(api, ASSISTANT).filter((s) => actions([s], "bedrock:").length));
    expect(resources).toContain("titan-embed-text-v2");
    expect(resources).toContain("claude-haiku-4-5");
    expect(resources).not.toContain("foundation-model/*");
  });

  it("stops before API Gateway's 30 s limit", () => {
    api.hasResourceProperties("AWS::Lambda::Function", { Handler: ASSISTANT, Timeout: 29 });
  });
});

describe("search indexer (DynamoDB stream → S3 Vectors)", () => {
  const INDEXER = "indexer.handler.handler";

  it("wakes only for read bills and deleted bills", () => {
    api.hasResourceProperties("AWS::Lambda::EventSourceMapping", {
      FilterCriteria: {
        Filters: Match.arrayWith([
          { Pattern: Match.stringLikeRegexp("ready") },
          { Pattern: Match.stringLikeRegexp("REMOVE") },
        ]),
      },
    });
  });

  it("takes two bills at a time, so a bill's 40 item embeddings fit well inside its timeout", () => {
    api.hasResourceProperties("AWS::Lambda::EventSourceMapping", {
      BatchSize: 2,
      FilterCriteria: Match.anyValue(),
    });
  });

  it("can write and delete vectors and embed text, nothing more", () => {
    const statements = statementsFor(api, INDEXER);
    expect(actions(statements, "s3vectors:")).toEqual([
      "s3vectors:DeleteVectors",
      "s3vectors:GetVectors",
      "s3vectors:PutVectors",
    ]);
    expect(actions(statements, "dynamodb:").filter((a) => !a.startsWith("dynamodb:DescribeStream") && !a.startsWith("dynamodb:GetRecords") && !a.startsWith("dynamodb:GetShardIterator") && !a.startsWith("dynamodb:ListStreams"))).toEqual([]);
  });
});
