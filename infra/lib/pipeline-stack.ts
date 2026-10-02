import { Duration, Stack, StackProps, Tags, TimeZone } from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as scheduler from "aws-cdk-lib/aws-scheduler";
import * as schedulerTargets from "aws-cdk-lib/aws-scheduler-targets";
import * as ses from "aws-cdk-lib/aws-ses";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import { grantInvokeModel } from "./bedrock";
import { newServiceFunction } from "./python-code";

export interface PipelineStackProps extends StackProps {
  readonly table: dynamodb.ITable;
  readonly docsBucket: s3.IBucket;
  readonly dataKey: kms.IKey;
  /** Bedrock cross-region inference profile ID, e.g. "us.anthropic.claude-haiku-4-5-…". */
  readonly bedrockModelId: string;
  /** Reminder emails look up the recipient's verified email here (not stored in DynamoDB). */
  readonly userPool: cognito.IUserPool;
  /** Linked from reminder emails. */
  readonly appUrl: string;
  /**
   * SSM parameter holding the reminder sender address, so it never appears in code or CI.
   * Create once: aws ssm put-parameter --name /onetally/sender-email --type String --value you@example.com
   * @default "/onetally/sender-email"
   */
  readonly senderEmailParameter?: string;
  readonly enableTracing?: boolean;
  /**
   * Daily upload limits (decision R2). `false` only for a testing deploy
   * (`cdk deploy -c dailyLimits=off`); any normal deploy turns them back on.
   * @default true
   */
  readonly dailyLimits?: boolean;
}

/**
 * Background processing: an uploaded file is read by Textract, structured by Claude on
 * Bedrock, checked by code, and saved with its deadlines.
 *
 * S3 Object Created → EventBridge rule → extract Lambda → Textract (sync, or async → SNS → same
 * Lambda) → Bedrock → DynamoDB. Failed events land in a dead-letter queue.
 *
 * Daily at 08:00 America/Los_Angeles, the reminders Lambda emails a digest of deadlines due in
 * 7, 3, and 1 days (GSI1 query → Cognito email lookup → SES).
 */
export class PipelineStack extends Stack {
  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onetally");

    const viaDynamo = `dynamodb.${this.region}.amazonaws.com`;
    const viaS3 = `s3.${this.region}.amazonaws.com`;

    // Events that fail delivery or keep failing in the Lambda wait here for inspection/replay.
    const deadLetters = new sqs.Queue(this, "ExtractDeadLetters", {
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });

    // Textract announces async job completion here; the message has job and object IDs only.
    const textractDone = new sns.Topic(this, "TextractDoneTopic", { enforceSSL: true });
    const textractPublishRole = new iam.Role(this, "TextractPublishRole", {
      description: "Lets Textract announce async job completion on the OneTally topic",
      assumedBy: new iam.ServicePrincipal("textract.amazonaws.com", {
        // Confused-deputy guard: only Textract jobs started from this account.
        conditions: {
          StringEquals: { "aws:SourceAccount": this.account },
          ArnLike: { "aws:SourceArn": `arn:${this.partition}:textract:${this.region}:${this.account}:*` },
        },
      }),
    });
    textractDone.grantPublish(textractPublishRole);
    // Nobody else may publish "job done" messages (the handler also checks them against the doc).
    textractDone.addToResourcePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ["sns:Publish"],
        resources: [textractDone.topicArn],
        conditions: { ArnNotEquals: { "aws:PrincipalArn": textractPublishRole.roleArn } },
      }),
    );

    const extractFn = newServiceFunction(this, "ExtractFunction", {
      handler: "documents.handler.handler",
      description: "Upload → Textract → Bedrock extraction → reconcile → validate → save DOC/OBL",
      serviceName: "onetally-documents",
      logKey: props.dataKey,
      memorySize: 512,
      // Fits Textract plus the Bedrock budget (2 calls × 2 attempts × 45 s read timeout).
      timeout: Duration.seconds(300),
      deadLetterQueue: deadLetters,
      enableTracing: props.enableTracing,
      environment: {
        TABLE_NAME: props.table.tableName,
        BUCKET_NAME: props.docsBucket.bucketName,
        BEDROCK_MODEL_ID: props.bedrockModelId,
        TEXTRACT_TOPIC_ARN: textractDone.topicArn,
        TEXTRACT_ROLE_ARN: textractPublishRole.roleArn,
        ...(props.dailyLimits === false ? { DAILY_LIMITS: "off" } : {}),
      },
    });

    new events.Rule(this, "UploadCreatedRule", {
      description: "New uploads (uploads/ prefix) trigger extraction",
      eventPattern: {
        source: ["aws.s3"],
        detailType: ["Object Created"],
        detail: {
          bucket: { name: [props.docsBucket.bucketName] },
          object: { key: [{ prefix: "uploads/" }] },
        },
      },
      targets: [
        new targets.LambdaFunction(extractFn, {
          deadLetterQueue: deadLetters,
          retryAttempts: 2,
          maxEventAge: Duration.hours(1),
        }),
      ],
    });
    textractDone.addSubscription(new subscriptions.LambdaSubscription(extractFn));

    // --- least-privilege permissions: exactly what documents/handler.py and store.py call ---
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Get/Update: state machine. Put/Delete: OBL# items inside the TransactWriteItems.
        actions: ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
        resources: [props.table.tableArn],
      }),
    );
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        // PDF checks read the object; Textract reads it with these same permissions. Reads pin the
        // claimed S3 version, which needs GetObjectVersion (GetObject alone is denied).
        actions: ["s3:GetObject", "s3:GetObjectVersion"],
        resources: [props.docsBucket.arnForObjects("uploads/*")],
      }),
    );
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
        resources: [props.dataKey.keyArn],
        conditions: { StringEquals: { "kms:ViaService": viaDynamo } },
      }),
    );
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Reading uploads only: decrypt, never encrypt, through S3.
        actions: ["kms:Decrypt"],
        resources: [props.dataKey.keyArn],
        conditions: { StringEquals: { "kms:ViaService": viaS3 } },
      }),
    );
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Textract doesn't support resource-level permissions for these APIs.
        actions: ["textract:AnalyzeExpense", "textract:StartExpenseAnalysis", "textract:GetExpenseAnalysis"],
        resources: ["*"],
      }),
    );
    extractFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["iam:PassRole"],
        resources: [textractPublishRole.roleArn],
        conditions: { StringEquals: { "iam:PassedToService": "textract.amazonaws.com" } },
      }),
    );
    grantInvokeModel(extractFn, props.bedrockModelId);

    // --- reminders ---------------------------------------------------------------------------
    const senderEmail = ssm.StringParameter.valueForStringParameter(
      this,
      props.senderEmailParameter ?? "/onetally/sender-email",
    );
    // SES emails a verification link to this address on first deploy; click it once.
    const sender = new ses.EmailIdentity(this, "ReminderSender", { identity: ses.Identity.email(senderEmail) });

    const remindersFn = newServiceFunction(this, "RemindersFunction", {
      handler: "reminders.handler.handler",
      description: "Daily digest of deadlines due in 7, 3, and 1 days",
      serviceName: "onetally-reminders",
      logKey: props.dataKey,
      timeout: Duration.seconds(120),
      enableTracing: props.enableTracing,
      environment: {
        TABLE_NAME: props.table.tableName,
        USER_POOL_ID: props.userPool.userPoolId,
        SENDER_EMAIL: senderEmail,
        APP_URL: props.appUrl,
      },
    });
    remindersFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:Query"],
        resources: [`${props.table.tableArn}/index/GSI1`],
      }),
    );
    remindersFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Get/Update: claim and un-claim each reminder offset on the OBL# item.
        // Put/Delete: roll a passed bill/subscription date forward (SUB# + its OBL#, in a transaction).
        actions: ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
        resources: [props.table.tableArn],
      }),
    );
    remindersFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
        resources: [props.dataKey.keyArn],
        conditions: { StringEquals: { "kms:ViaService": viaDynamo } },
      }),
    );
    remindersFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ["cognito-idp:ListUsers"], resources: [props.userPool.userPoolArn] }),
    );
    remindersFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ses:SendEmail"],
        resources: [`arn:${this.partition}:ses:${this.region}:${this.account}:identity/${sender.emailIdentityName}`],
      }),
    );

    new scheduler.Schedule(this, "DailyReminders", {
      description: "Reminder digest, every morning",
      schedule: scheduler.ScheduleExpression.cron({
        minute: "0",
        hour: "8",
        timeZone: TimeZone.AMERICA_LOS_ANGELES,
      }),
      target: new schedulerTargets.LambdaInvoke(remindersFn, {
        retryAttempts: 2,
        maxEventAge: Duration.hours(2),
        deadLetterQueue: deadLetters,
      }),
    });
  }
}
