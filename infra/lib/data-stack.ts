import { Duration, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

/**
 * Storage for every user's data: one single-table DynamoDB table (records) and one
 * S3 bucket (documents), both encrypted with a customer-managed KMS key.
 * Key design: docs/SPEC.md → Data model.
 */
export interface DataStackProps extends StackProps {
  /** Browser origin allowed to POST uploads straight to the docs bucket (presigned POST). */
  readonly frontendUrl: string;
}

export class DataStack extends Stack {
  readonly key: kms.Key;
  readonly table: dynamodb.Table;
  readonly docsBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onetally");

    this.key = new kms.Key(this, "DataKey", {
      alias: "alias/onetally",
      description: "OneTally: encrypts the main table, the docs bucket, and (later) Plaid tokens",
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Let CloudWatch Logs encrypt this account's log groups with the key, so log contents get
    // the same protection (and kms:Decrypt restrictions) as the table and bucket.
    this.key.addToResourcePolicy(
      new iam.PolicyStatement({
        principals: [new iam.ServicePrincipal(`logs.${this.region}.amazonaws.com`)],
        actions: ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"],
        resources: ["*"], // in a key policy, "*" means "this key"
        conditions: {
          ArnLike: {
            "kms:EncryptionContext:aws:logs:arn": `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:*`,
          },
        },
      }),
    );

    this.table = new dynamodb.Table(this, "Table", {
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: this.key,
      deletionProtection: true,
      // Items with an `expiresAt` epoch-seconds attribute (e.g. daily usage counters) delete themselves.
      timeToLiveAttribute: "expiresAt",
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
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      bucketKeyEnabled: true,
      // Versioning protects documents from accidental/malicious overwrites and deletes.
      versioned: true,
      lifecycleRules: [
        { id: "expire-tmp", prefix: "tmp/", expiration: Duration.days(1) },
        { id: "abort-incomplete-uploads", abortIncompleteMultipartUploadAfter: Duration.days(1) },
        { id: "expire-old-versions", noncurrentVersionExpiration: Duration.days(30) },
      ],
      // "Object Created" events go to EventBridge, where a rule routes uploads/ to the extract Lambda.
      eventBridgeEnabled: true,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.POST],
          allowedOrigins: [props.frontendUrl],
          allowedHeaders: ["*"],
          maxAge: 3000,
        },
      ],
      removalPolicy: RemovalPolicy.RETAIN,
    });
  }
}
