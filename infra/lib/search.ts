import { Duration } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { DynamoEventSource, SqsDlq } from "aws-cdk-lib/aws-lambda-event-sources";
import * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";
import { grantEmbeddings } from "./bedrock";
import { newServiceFunction } from "./python-code";

export interface BillsSearchProps {
  readonly table: dynamodb.ITable;
  readonly enableTracing?: boolean;
}

/**
 * Semantic search over bills (RAG) for "Ask OneBill": one 512-dimension Titan embedding per read
 * bill in S3 Vectors, keyed `<userId>#<docId>` with the owner's userId as filterable metadata.
 * An indexer on the table's stream keeps it in step.
 *
 * Lives with the assistant (not the data stack): it's derived data the indexer can rebuild, and
 * keeping it in one stack means a settings change that replaces the index never has to unlock a
 * cross-stack export. Pay per use, no cluster: pennies at this size.
 */
export class BillsSearch extends Construct {
  readonly indexArn: string;

  constructor(scope: Construct, id: string, props: BillsSearchProps) {
    super(scope, id);

    const vectorBucket = new s3vectors.CfnVectorBucket(this, "Vectors", {});
    const index = new s3vectors.CfnIndex(this, "Index", {
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      // No fixed name: a settings change replaces the index, and CloudFormation creates the new
      // one before deleting the old, so a fixed name would collide.
      dataType: "float32",
      dimension: 512,
      distanceMetric: "cosine",
      // Only userId is filtered on; the rest is returned, not searched.
      metadataConfiguration: { nonFilterableMetadataKeys: ["docId", "date", "textHash"] },
    });
    this.indexArn = index.attrIndexArn;

    // Read bills are embedded; deleted ones leave the index. The filters mean the Lambda only
    // wakes for read DOC# items and DOC# deletions, not every table write.
    const indexer = newServiceFunction(this, "IndexerFunction", {
      handler: "indexer.handler.handler",
      description: "Keeps the bills search index (S3 Vectors) in step with the table",
      serviceName: "onebill-indexer",
      timeout: Duration.seconds(60),
      enableTracing: props.enableTracing,
      environment: { VECTOR_INDEX_ARN: this.indexArn },
    });
    grantEmbeddings(indexer);
    indexer.addToRolePolicy(
      new iam.PolicyStatement({
        // GetVectors: compare the stored text fingerprint, to skip unchanged bills.
        actions: ["s3vectors:PutVectors", "s3vectors:DeleteVectors", "s3vectors:GetVectors"],
        resources: [this.indexArn],
      }),
    );
    const failed = new sqs.Queue(this, "IndexerDeadLetters", {
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });
    indexer.addEventSource(
      new DynamoEventSource(props.table, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        retryAttempts: 2,
        onFailure: new SqsDlq(failed),
        filters: [
          lambda.FilterCriteria.filter({
            eventName: lambda.FilterRule.or("INSERT", "MODIFY"),
            dynamodb: {
              NewImage: {
                SK: { S: lambda.FilterRule.beginsWith("DOC#") },
                status: { S: lambda.FilterRule.or("ready", "needs_review") },
              },
            },
          }),
          lambda.FilterCriteria.filter({
            eventName: lambda.FilterRule.isEqual("REMOVE"),
            dynamodb: { Keys: { SK: { S: lambda.FilterRule.beginsWith("DOC#") } } },
          }),
        ],
      }),
    );
  }
}
