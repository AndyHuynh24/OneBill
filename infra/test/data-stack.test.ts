import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { DataStack } from "../lib/data-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  const stack = new DataStack(app, "TestData", { frontendUrls: ["http://localhost:5173"] });
  template = Template.fromStack(stack);
});

describe("DataStack — encryption", () => {
  it("creates no customer-managed key (AWS-owned keys encrypt the table and bucket)", () => {
    template.resourceCountIs("AWS::KMS::Key", 0);
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

  it("is encrypted at rest with an AWS-owned key (no customer-managed key)", () => {
    const table = Object.values(template.findResources("AWS::DynamoDB::Table"))[0].Properties;
    expect(table.SSESpecification?.SSEType).toBeUndefined();
    expect(table.SSESpecification?.KMSMasterKeyId).toBeUndefined();
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

  it("is encrypted at rest with S3-managed keys", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [{ ServerSideEncryptionByDefault: { SSEAlgorithm: "AES256" } }],
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

describe("DataStack — upload pipeline settings", () => {
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
  it("tags resources with project=onebill", () => {
    const tables = template.findResources("AWS::DynamoDB::Table");
    const tags = Object.values(tables)[0].Properties.Tags;
    expect(tags).toContainEqual({ Key: "project", Value: "onebill" });
  });
});

describe("DataStack — receipt photos aren't kept", () => {
  it("removes anything left under uploads/ within a day, every version", () => {
    const bucket = Object.values(template.findResources("AWS::S3::Bucket")).find((b) => b.Properties.CorsConfiguration);
    const rule = bucket?.Properties.LifecycleConfiguration.Rules.find((r: { Id: string }) => r.Id === "expire-uploads");
    expect(rule).toMatchObject({ Prefix: "uploads/", ExpirationInDays: 1, NoncurrentVersionExpiration: { NoncurrentDays: 1 } });
  });
});

