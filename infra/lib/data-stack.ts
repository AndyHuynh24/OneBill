import { Duration, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

/**
 * Storage for every user's data: one single-table DynamoDB table (records) and one
 * S3 bucket (documents), both encrypted at rest with AWS-owned keys.
 * Key design: docs/SPEC.md → Data model.
 */
export interface DataStackProps extends StackProps {
  /** Browser origin allowed to POST uploads straight to the docs bucket (presigned POST). */
  /** Origins allowed to upload straight to the docs bucket (presigned POST). */
  readonly frontendUrls: string[];
}

export class DataStack extends Stack {
  readonly table: dynamodb.Table;
  readonly docsBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onebill");

    this.table = new dynamodb.Table(this, "Table", {
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      // Encrypted at rest with AWS's own key (free). A customer-managed key only adds a second
      // lock against roles with partial access; with one user and no AWS-connected CI, there
      // are none (decision 2026-10-04). Add one back before granting anyone limited access.
      encryption: dynamodb.TableEncryption.DEFAULT,
      deletionProtection: true,
      // Items with an `expiresAt` epoch-seconds attribute (e.g. daily usage counters) delete themselves.
      timeToLiveAttribute: "expiresAt",
      // New items flow to a stream: the reminders Lambda emails right away when a new reminder
      // is already due within 7 days (it only wakes for OBL# inserts).
      stream: dynamodb.StreamViewType.NEW_IMAGE,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // GSI1: obligations by due date (daily reminder job). Sparse: only OBL# items set GSI1PK.
    this.table.addGlobalSecondaryIndex({
      indexName: "GSI1",
      partitionKey: { name: "GSI1PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "GSI1SK", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI2: a user's groups. Sparse: only MEMBER# items set GSI2PK.
    this.table.addGlobalSecondaryIndex({
      indexName: "GSI2",
      partitionKey: { name: "GSI2PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "GSI2SK", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.docsBucket = new s3.Bucket(this, "DocsBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      minimumTLSVersion: 1.2,
      encryption: s3.BucketEncryption.S3_MANAGED, // encrypted at rest with AWS's own key (free)
      // Versioning protects documents from accidental/malicious overwrites and deletes.
      versioned: true,
      lifecycleRules: [
        { id: "expire-tmp", prefix: "tmp/", expiration: Duration.days(1) },
        // Receipt photos and PDFs aren't kept: deleted right after reading, and this removes
        // anything a failed delete left behind (every version) within a day.
        {
          id: "expire-uploads",
          prefix: "uploads/",
          expiration: Duration.days(1),
          noncurrentVersionExpiration: Duration.days(1),
        },
        { id: "abort-incomplete-uploads", abortIncompleteMultipartUploadAfter: Duration.days(1) },
        { id: "expire-old-versions", noncurrentVersionExpiration: Duration.days(30) },
      ],
      cors: [
        {
          allowedMethods: [s3.HttpMethods.POST],
          allowedOrigins: props.frontendUrls,
          allowedHeaders: ["*"],
          maxAge: 3000,
        },
      ],
      removalPolicy: RemovalPolicy.RETAIN,
    });
    // "Object Created" events go to EventBridge, where a rule routes uploads/ to the extract
    // Lambda. Set as a plain bucket property: `eventBridgeEnabled: true` would add a
    // custom-resource Lambda (with a never-expiring log group) just to flip this switch.
    (this.docsBucket.node.defaultChild as s3.CfnBucket).notificationConfiguration = {
      eventBridgeConfiguration: { eventBridgeEnabled: true },
    };
  }
}
