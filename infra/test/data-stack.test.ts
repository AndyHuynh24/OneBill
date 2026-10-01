import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { DataStack } from "../lib/data-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  const stack = new DataStack(app, "TestData", { frontendUrl: "http://localhost:5173" });
  template = Template.fromStack(stack);
});

describe("DataStack — KMS key", () => {
  it("creates one customer-managed key with rotation, retained on delete", () => {
    template.resourceCountIs("AWS::KMS::Key", 1);
    template.hasResource("AWS::KMS::Key", {
      Properties: { EnableKeyRotation: true },
      DeletionPolicy: "Retain",
      UpdateReplacePolicy: "Retain",
    });
    template.hasResourceProperties("AWS::KMS::Alias", { AliasName: "alias/onetally" });
  });

  it("lets CloudWatch Logs use the key only for this account's log groups", () => {
    template.hasResourceProperties("AWS::KMS::Key", {
      KeyPolicy: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Principal: { Service: Match.objectLike({ "Fn::Join": Match.anyValue() }) },
            Condition: {
              ArnLike: { "kms:EncryptionContext:aws:logs:arn": Match.anyValue() },
            },
          }),
        ]),
      },
    });
  });
});

describe("DataStack — DynamoDB table", () => {
  it("uses generic PK/SK string keys", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      KeySchema: [
        { AttributeName: "PK", KeyType: "HASH" },
        { AttributeName: "SK", KeyType: "RANGE" },
      ],
      AttributeDefinitions: Match.arrayWith([
        { AttributeName: "PK", AttributeType: "S" },
        { AttributeName: "SK", AttributeType: "S" },
      ]),
    });
  });

  it("has GSI1 and GSI2 with overloaded keys and ALL projection", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      GlobalSecondaryIndexes: [
        {
          IndexName: "GSI1",
          KeySchema: [
            { AttributeName: "GSI1PK", KeyType: "HASH" },
            { AttributeName: "GSI1SK", KeyType: "RANGE" },
          ],
          Projection: { ProjectionType: "ALL" },
        },
        {
          IndexName: "GSI2",
          KeySchema: [
            { AttributeName: "GSI2PK", KeyType: "HASH" },
            { AttributeName: "GSI2SK", KeyType: "RANGE" },
          ],
          Projection: { ProjectionType: "ALL" },
        },
      ],
    });
  });

  it("is on-demand, with PITR and deletion protection", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      BillingMode: "PAY_PER_REQUEST",
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
      DeletionProtectionEnabled: true,
    });
  });

  it("is encrypted with the customer-managed key", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      SSESpecification: {
        SSEEnabled: true,
        SSEType: "KMS",
        KMSMasterKeyId: Match.anyValue(),
      },
    });
  });

  it("is retained when the stack is deleted", () => {
    template.hasResource("AWS::DynamoDB::Table", {
      DeletionPolicy: "Retain",
      UpdateReplacePolicy: "Retain",
    });
  });
});

describe("DataStack — S3 docs bucket", () => {
  it("blocks all public access", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  it("uses SSE-KMS with the customer-managed key and a bucket key", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          {
            BucketKeyEnabled: true,
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: "aws:kms",
              KMSMasterKeyID: Match.anyValue(),
            },
          },
        ],
      },
    });
  });

  it("expires tmp/ objects after one day", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({ Prefix: "tmp/", ExpirationInDays: 1, Status: "Enabled" }),
        ]),
      },
    });
  });

  it("is versioned, expiring old versions after 30 days", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      VersioningConfiguration: { Status: "Enabled" },
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({ NoncurrentVersionExpiration: { NoncurrentDays: 30 }, Status: "Enabled" }),
        ]),
      },
    });
  });

  it("denies non-TLS requests via bucket policy", () => {
    template.hasResourceProperties("AWS::S3::BucketPolicy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: "Deny",
            Condition: { Bool: { "aws:SecureTransport": "false" } },
          }),
        ]),
      },
    });
  });

  it("is retained when the stack is deleted", () => {
    template.hasResource("AWS::S3::Bucket", { DeletionPolicy: "Retain" });
  });
});

describe("DataStack — Phase 1 upload pipeline settings", () => {
  it("sends bucket events to EventBridge via a bucket property (no custom-resource Lambda)", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      NotificationConfiguration: { EventBridgeConfiguration: { EventBridgeEnabled: true } },
    });
    template.resourceCountIs("Custom::S3BucketNotifications", 0);
    template.resourceCountIs("AWS::Lambda::Function", 0);
  });

  it("allows browser POST uploads only from the frontend origin", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      CorsConfiguration: {
        CorsRules: [
          Match.objectLike({
            AllowedMethods: ["POST"],
            AllowedOrigins: ["http://localhost:5173"],
          }),
        ],
      },
    });
  });

  it("expires usage counters with DynamoDB TTL on expiresAt", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TimeToLiveSpecification: { AttributeName: "expiresAt", Enabled: true },
    });
  });
});

describe("DataStack — tagging", () => {
  it("tags resources with project=onetally", () => {
    const tables = template.findResources("AWS::DynamoDB::Table");
    const tags = Object.values(tables)[0].Properties.Tags;
    expect(tags).toContainEqual({ Key: "project", Value: "onetally" });
  });
});

describe("DataStack — dev origins", () => {
  it("lets dev origins upload with the presigned POST", () => {
    const app = new App();
    const stack = new DataStack(app, "DevData", {
      frontendUrl: "http://localhost:5173",
      devOrigins: ["https://10.0.0.136:5173"],
    });

    Template.fromStack(stack).hasResourceProperties("AWS::S3::Bucket", {
      CorsConfiguration: {
        CorsRules: [Match.objectLike({ AllowedOrigins: ["http://localhost:5173", "https://10.0.0.136:5173"] })],
      },
    });
  });
});
