import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { ApiStack } from "../lib/api-stack";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";

let template: Template;

type Statement = { Action: string | string[]; Resource: unknown; Condition?: unknown };

/** IAM statements attached to the role of the Lambda whose handler is `handler`. */
function statementsFor(handler: string): Statement[] {
  const fns = template.findResources("AWS::Lambda::Function", { Properties: { Handler: handler } });
  const roleId = Object.values(fns)[0].Properties.Role["Fn::GetAtt"][0];
  const policies = Object.values(template.findResources("AWS::IAM::Policy")).filter((p) =>
    p.Properties.Roles.some((r: { Ref: string }) => r.Ref === roleId),
  );
  return policies.flatMap((p) => p.Properties.PolicyDocument.Statement);
}

const actionsOf = (statements: Statement[], prefix: string) =>
  statements.flatMap((st) => [st.Action].flat()).filter((a) => a.startsWith(prefix)).sort();

beforeAll(() => {
  const app = new App();
  const env = { account: "123456789012", region: "us-east-1" };
  const data = new DataStack(app, "TestData", { env, frontendUrl: "http://localhost:5173" });
  const auth = new AuthStack(app, "TestAuth", {
    env,
    domainPrefix: "onebill-test",
    frontendUrl: "http://localhost:5173",
  });
  const api = new ApiStack(app, "TestApi", {
    env,
    table: data.table,
    docsBucket: data.docsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.client,
    frontendUrl: "http://localhost:5173",
    bedrockModelId: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  });
  template = Template.fromStack(api);
});

describe("ApiStack — HTTP API", () => {
  it("is an HTTP API with CORS limited to the frontend origin", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Api", {
      ProtocolType: "HTTP",
      CorsConfiguration: Match.objectLike({
        AllowOrigins: ["http://localhost:5173"],
        AllowMethods: ["GET", "POST", "PATCH", "PUT", "DELETE"],
        AllowHeaders: ["authorization", "content-type"],
      }),
    });
  });

  it("protects GET /me with the JWT authorizer, accepting access tokens only", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "GET /me",
      AuthorizationType: "JWT",
      AuthorizerId: Match.anyValue(),
      // ID tokens have no `scope` claim, so requiring a scope rejects them.
      AuthorizationScopes: ["openid"],
    });
  });

  it("keeps recent friends off the server (they live on the device)", () => {
    const routes = Object.values(template.findResources("AWS::ApiGatewayV2::Route"));
    expect(routes.some((r) => String(r.Properties.RouteKey).includes("friends"))).toBe(false);
  });

  it("requires a Cognito sign-in on every route except the Gmail script's ingest route", () => {
    const routes = Object.values(template.findResources("AWS::ApiGatewayV2::Route"));
    const open = routes
      .filter((r) => r.Properties.AuthorizationType !== "JWT")
      .map((r) => r.Properties.RouteKey);
    // POST /ingest/email checks the hashed ingest key in code (Apps Script can't sign in).
    expect(open).toEqual(["POST /ingest/email"]);
  });

  it("configures the authorizer with the Cognito issuer and app client audience", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Authorizer", {
      AuthorizerType: "JWT",
      IdentitySource: ["$request.header.Authorization"],
      JwtConfiguration: {
        Audience: [Match.anyValue()],
        Issuer: Match.objectLike({
          "Fn::Join": ["", Match.arrayWith(["https://cognito-idp.us-east-1.amazonaws.com/"])],
        }),
      },
    });
  });

  it("throttles the default stage and writes access logs", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Stage", {
      StageName: "$default",
      AutoDeploy: true,
      DefaultRouteSettings: { ThrottlingRateLimit: 2, ThrottlingBurstLimit: 5 },
      AccessLogSettings: { DestinationArn: Match.anyValue(), Format: Match.stringLikeRegexp("requestId") },
    });
  });
});

describe("ApiStack — /me Lambda", () => {
  it("runs Python 3.12 on arm64 with Powertools and small limits", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Runtime: "python3.12",
      Architectures: ["arm64"],
      Handler: "api.me.handler.handler",
      MemorySize: 256,
      Timeout: 10,
      Layers: [Match.stringLikeRegexp("AWSLambdaPowertoolsPythonV3-python312-arm64")],
      Environment: {
        Variables: Match.objectLike({
          TABLE_NAME: Match.anyValue(),
          // Keep response bodies (future: transactions) out of X-Ray traces.
          POWERTOOLS_TRACER_CAPTURE_RESPONSE: "false",
          // X-Ray is off by default (cost decision R1); enable with -c tracing=true.
          POWERTOOLS_TRACE_DISABLED: "true",
        }),
      },
    });
  });

  it("does not enable active X-Ray tracing by default", () => {
    const fns = Object.values(template.findResources("AWS::Lambda::Function"));
    for (const fn of fns) {
      expect(fn.Properties.TracingConfig).toBeUndefined();
    }
  });

  it("keeps all logs for two weeks (encrypted at rest by CloudWatch)", () => {
    const groups = Object.values(template.findResources("AWS::Logs::LogGroup"));
    expect(groups.length).toBeGreaterThan(0);
    for (const group of groups) {
      expect(group.Properties.RetentionInDays).toBe(14);
    }
  });

  it("grants /me only GetItem and PutItem on the table itself (no Scan, no indexes)", () => {
    const statements = statementsFor("api.me.handler.handler");
    expect(actionsOf(statements, "dynamodb:")).toEqual(["dynamodb:GetItem", "dynamodb:PutItem"]);
    const dynamo = statements.find((st) => actionsOf([st], "dynamodb:").length);
    expect(JSON.stringify(dynamo?.Resource)).not.toContain("index");
    expect(actionsOf(statements, "s3:")).toEqual([]);
  });

  it("needs no KMS permissions: data is encrypted with AWS-owned keys", () => {
    const actions = Object.values(template.findResources("AWS::IAM::Policy")).flatMap((p) =>
      p.Properties.PolicyDocument.Statement.flatMap((st: { Action: string | string[] }) => [st.Action].flat()),
    );
    expect(actions.filter((a: string) => a.startsWith("kms:"))).toEqual([]);
  });

  it("never grants wildcard actions", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const policy of Object.values(policies)) {
      for (const stmt of policy.Properties.PolicyDocument.Statement) {
        expect([stmt.Action].flat()).not.toContain("*");
      }
    }
  });
});

describe("ApiStack — documents API", () => {
  it.each([
    "POST /documents",
    "GET /documents",
    "GET /documents/{docId}",
    "PATCH /documents/{docId}",
    "GET /obligations",
    "PUT /documents/{docId}/split",
    "GET /subscriptions",
    "POST /subscriptions",
    "PATCH /subscriptions/{key}",
    "POST /ingest/keys",
    "DELETE /ingest/keys",
    "DELETE /subscriptions/{key}",
    "DELETE /obligations/{oblId}",
    "DELETE /documents/{docId}",
  ])(
    "protects %s with the JWT authorizer",
    (routeKey) => {
      template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
        RouteKey: routeKey,
        AuthorizationType: "JWT",
        AuthorizationScopes: ["openid"],
      });
    },
  );

  it("runs the documents Lambda with the table and bucket names", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "api.documents.handler.handler",
      Runtime: "python3.12",
      Architectures: ["arm64"],
      Environment: {
        Variables: Match.objectLike({ TABLE_NAME: Match.anyValue(), BUCKET_NAME: Match.anyValue() }),
      },
    });
  });

  it("grants documents only the DynamoDB calls it makes: no Scan, no indexes", () => {
    const statements = statementsFor("api.documents.handler.handler");
    expect(actionsOf(statements, "dynamodb:")).toEqual([
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]);
    const dynamo = statements.find((st) => actionsOf([st], "dynamodb:").length);
    expect(JSON.stringify(dynamo?.Resource)).not.toContain("index");
  });

  it("lets documents sign uploads and delete them, only under uploads/ (nothing else in S3)", () => {
    const statements = statementsFor("api.documents.handler.handler");
    expect(actionsOf(statements, "s3:")).toEqual(["s3:DeleteObject", "s3:PutObject"]);
    const s3 = statements.find((st) => actionsOf([st], "s3:").length);
    expect(JSON.stringify(s3?.Resource)).toContain("/uploads/*");
  });

});

describe("ApiStack — tracing switch", () => {
  it("turns on active X-Ray tracing when enableTracing is set", () => {
    const app = new App();
    const env = { account: "123456789012", region: "us-east-1" };
    const data = new DataStack(app, "D", { env, frontendUrl: "http://localhost:5173" });
    const auth = new AuthStack(app, "A", { env, domainPrefix: "x", frontendUrl: "http://localhost:5173" });
    const api = new ApiStack(app, "Api", {
      env,
      table: data.table,
      docsBucket: data.docsBucket,
      userPool: auth.userPool,
      userPoolClient: auth.client,
      frontendUrl: "http://localhost:5173",
      bedrockModelId: "us.m",
      enableTracing: true,
    });
    const t = Template.fromStack(api);
    t.hasResourceProperties("AWS::Lambda::Function", {
      TracingConfig: { Mode: "Active" },
      Environment: { Variables: Match.objectLike({ POWERTOOLS_TRACE_DISABLED: "false" }) },
    });
  });
});
