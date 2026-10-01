import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { AuthStack } from "../lib/auth-stack";
import { DataStack } from "../lib/data-stack";
import { PipelineStack } from "../lib/pipeline-stack";

const MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0";
let template: Template;

beforeAll(() => {
  const app = new App();
  const env = { account: "123456789012", region: "us-east-1" };
  const data = new DataStack(app, "TestData", { env, frontendUrl: "http://localhost:5173" });
  const auth = new AuthStack(app, "TestAuth", { env, domainPrefix: "x", frontendUrl: "http://localhost:5173" });
  const pipeline = new PipelineStack(app, "TestPipeline", {
    env,
    table: data.table,
    docsBucket: data.docsBucket,
    dataKey: data.key,
    bedrockModelId: MODEL,
    userPool: auth.userPool,
    appUrl: "http://localhost:5173",
  });
  template = Template.fromStack(pipeline);
});

type Statement = { Action: string | string[]; Resource: unknown; Condition?: Record<string, unknown> };

function extractStatements(): Statement[] {
  return statementsFor("documents.handler.handler");
}

function statementsFor(handler: string): Statement[] {
  const fns = template.findResources("AWS::Lambda::Function", { Properties: { Handler: handler } });
  const roleId = Object.values(fns)[0].Properties.Role["Fn::GetAtt"][0];
  return Object.values(template.findResources("AWS::IAM::Policy"))
    .filter((p) => p.Properties.Roles.some((r: { Ref: string }) => r.Ref === roleId))
    .flatMap((p) => p.Properties.PolicyDocument.Statement);
}

const actionsOf = (statements: Statement[], prefix: string) =>
  statements.flatMap((st) => [st.Action].flat()).filter((a) => a.startsWith(prefix)).sort();

describe("PipelineStack — upload trigger", () => {
  it("routes S3 'Object Created' events under uploads/ to the extract Lambda", () => {
    template.hasResourceProperties("AWS::Events::Rule", {
      EventPattern: {
        source: ["aws.s3"],
        "detail-type": ["Object Created"],
        detail: {
          bucket: { name: [Match.anyValue()] },
          object: { key: [{ prefix: "uploads/" }] },
        },
      },
    });
  });

  it("retries delivery and sends undeliverable events to a dead-letter queue", () => {
    template.hasResourceProperties("AWS::Events::Rule", {
      Targets: [
        Match.objectLike({
          RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 },
          DeadLetterConfig: { Arn: Match.anyValue() },
        }),
      ],
    });
  });

  it("also sends failed async invocations of the Lambda to the dead-letter queue", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "documents.handler.handler",
      DeadLetterConfig: { TargetArn: Match.anyValue() },
    });
  });

  it("keeps dead letters for 14 days, TLS-only", () => {
    template.hasResourceProperties("AWS::SQS::Queue", { MessageRetentionPeriod: 1209600 });
    template.hasResourceProperties("AWS::SQS::QueuePolicy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({ Effect: "Deny", Condition: { Bool: { "aws:SecureTransport": "false" } } }),
        ]),
      },
    });
  });
});

describe("PipelineStack — extract Lambda", () => {
  it("has the environment the handler reads, and room for OCR + two model calls", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "documents.handler.handler",
      Runtime: "python3.12",
      Architectures: ["arm64"],
      MemorySize: 512,
      Timeout: 180,
      Environment: {
        Variables: Match.objectLike({
          TABLE_NAME: Match.anyValue(),
          BUCKET_NAME: Match.anyValue(),
          BEDROCK_MODEL_ID: MODEL,
          TEXTRACT_TOPIC_ARN: Match.anyValue(),
          TEXTRACT_ROLE_ARN: Match.anyValue(),
        }),
      },
    });
  });

  it("gets only the DynamoDB actions its state machine and transaction use", () => {
    expect(actionsOf(extractStatements(), "dynamodb:")).toEqual([
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]);
  });

  it("can only read uploads (no writes, no other prefixes)", () => {
    const statements = extractStatements();
    expect(actionsOf(statements, "s3:")).toEqual(["s3:GetObject"]);
    const s3 = statements.find((st) => actionsOf([st], "s3:").length);
    expect(JSON.stringify(s3?.Resource)).toContain("/uploads/*");
  });

  it("calls only the three Textract expense APIs", () => {
    expect(actionsOf(extractStatements(), "textract:")).toEqual([
      "textract:AnalyzeExpense",
      "textract:GetExpenseAnalysis",
      "textract:StartExpenseAnalysis",
    ]);
  });

  it("may invoke only the configured Haiku inference profile and its underlying model", () => {
    const bedrock = extractStatements().filter((st) => actionsOf([st], "bedrock:").length);
    const resources = JSON.stringify(bedrock.map((st) => st.Resource));
    expect(actionsOf(bedrock, "bedrock:")).toEqual(["bedrock:InvokeModel", "bedrock:InvokeModel"]);
    expect(resources).toContain(`inference-profile/${MODEL}`);
    expect(resources).toContain("foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0");
    const viaProfile = bedrock.find((st) => JSON.stringify(st.Resource).includes("foundation-model"));
    expect(JSON.stringify(viaProfile?.Condition)).toContain("bedrock:InferenceProfileArn");
  });

  it("may pass the Textract role only to Textract", () => {
    const pass = extractStatements().find((st) => actionsOf([st], "iam:").length);
    expect(pass?.Action).toBe("iam:PassRole");
    expect(pass?.Condition).toEqual({ StringEquals: { "iam:PassedToService": "textract.amazonaws.com" } });
  });

  it("uses the data key only through S3 and DynamoDB", () => {
    const kms = extractStatements().filter((st) => actionsOf([st], "kms:").length);
    expect(kms).toHaveLength(1);
    expect(kms[0].Condition).toEqual({
      StringEquals: {
        "kms:ViaService": ["dynamodb.us-east-1.amazonaws.com", "s3.us-east-1.amazonaws.com"],
      },
    });
  });

  it("never grants wildcard actions", () => {
    for (const st of extractStatements()) {
      expect([st.Action].flat()).not.toContain("*");
    }
  });
});

describe("PipelineStack — Textract completion (SNS)", () => {
  it("subscribes the extract Lambda to the completion topic", () => {
    template.hasResourceProperties("AWS::SNS::Subscription", { Protocol: "lambda" });
  });

  it("lets only Textract, for this account, assume the publish role", () => {
    template.hasResourceProperties("AWS::IAM::Role", {
      AssumeRolePolicyDocument: {
        Statement: [
          Match.objectLike({
            Principal: { Service: "textract.amazonaws.com" },
            Condition: {
              StringEquals: { "aws:SourceAccount": "123456789012" },
              ArnLike: { "aws:SourceArn": Match.anyValue() },
            },
          }),
        ],
      },
    });
  });

  it("the publish role can publish only to the completion topic", () => {
    const roles = template.findResources("AWS::IAM::Role", {
      Properties: { AssumeRolePolicyDocument: { Statement: [Match.objectLike({ Principal: { Service: "textract.amazonaws.com" } })] } },
    });
    const roleId = Object.keys(roles)[0];
    const statements = Object.values(template.findResources("AWS::IAM::Policy"))
      .filter((p) => p.Properties.Roles.some((r: { Ref: string }) => r.Ref === roleId))
      .flatMap((p) => p.Properties.PolicyDocument.Statement);
    expect(statements).toHaveLength(1);
    expect(statements[0].Action).toBe("sns:Publish");
    expect(JSON.stringify(statements[0].Resource)).toMatch(/"Ref":"TextractDoneTopic/);
  });

  it("topic accepts only TLS", () => {
    template.hasResourceProperties("AWS::SNS::TopicPolicy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({ Effect: "Deny", Condition: { Bool: { "aws:SecureTransport": "false" } } }),
        ]),
      },
    });
  });
});

describe("PipelineStack — reminders", () => {
  it("runs daily at 08:00 Los Angeles time, with retries and a dead-letter queue", () => {
    template.hasResourceProperties("AWS::Scheduler::Schedule", {
      ScheduleExpression: "cron(0 8 * * ? *)",
      ScheduleExpressionTimezone: "America/Los_Angeles",
      Target: Match.objectLike({
        RetryPolicy: Match.objectLike({ MaximumRetryAttempts: 2 }),
        DeadLetterConfig: { Arn: Match.anyValue() },
      }),
    });
  });

  it("verifies the sender address in SES, read from SSM (never in code)", () => {
    template.hasResourceProperties("AWS::SES::EmailIdentity", {
      EmailIdentity: { Ref: Match.stringLikeRegexp("SsmParameterValue") },
    });
    template.hasParameter("*", {
      Type: "AWS::SSM::Parameter::Value<String>",
      Default: "/onetally/sender-email",
    });
  });

  it("gives the reminders Lambda the pool, sender, and app URL", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "reminders.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          TABLE_NAME: Match.anyValue(),
          USER_POOL_ID: Match.anyValue(),
          SENDER_EMAIL: Match.anyValue(),
          APP_URL: "http://localhost:5173",
        }),
      },
    });
  });

  it("can query only the due-date index, and update obligations", () => {
    const statements = statementsFor("reminders.handler.handler");
    expect(actionsOf(statements, "dynamodb:")).toEqual([
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]);
    const query = statements.find((st) => [st.Action].flat().includes("dynamodb:Query"));
    expect(JSON.stringify(query?.Resource)).toContain("/index/GSI1");
  });

  it("can only look up users in the OneTally pool and send from the verified sender", () => {
    const statements = statementsFor("reminders.handler.handler");
    expect(actionsOf(statements, "cognito-idp:")).toEqual(["cognito-idp:ListUsers"]);
    expect(actionsOf(statements, "ses:")).toEqual(["ses:SendEmail"]);
    const ses = statements.find((st) => actionsOf([st], "ses:").length);
    expect(JSON.stringify(ses?.Resource)).toContain(":identity/");
    for (const st of statements) {
      expect([st.Action].flat()).not.toContain("*");
    }
  });
});

describe("PipelineStack — logs", () => {
  it("encrypts and expires all log groups", () => {
    for (const group of Object.values(template.findResources("AWS::Logs::LogGroup"))) {
      expect(group.Properties.RetentionInDays).toBe(14);
      expect(group.Properties.KmsKeyId).toBeDefined();
    }
  });
});
