import { Duration, Stack, StackProps, Tags, TimeZone } from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
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
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import { alarmOver, HOURLY } from "./alerts";
import { newServiceFunction, pillowLayer } from "./python-code";

export interface PipelineStackProps extends StackProps {
  readonly table: dynamodb.ITable;
  readonly docsBucket: s3.IBucket;
  /** Bedrock cross-region inference profile ID, e.g. "us.anthropic.claude-haiku-4-5-…". */
  readonly bedrockModelId: string;
  /** Reminder emails look up the recipient's verified email here (not stored in DynamoDB). */
  readonly userPool: cognito.IUserPool;
  /** Linked from reminder emails. */
  readonly appUrl: string;
  /**
   * SSM parameter holding the reminder sender address, so it never appears in code or CI.
   * Create once: aws ssm put-parameter --name /onebill/sender-email --type String --value you@example.com
   * @default "/onebill/sender-email"
   */
  readonly senderEmailParameter?: string;
  readonly enableTracing?: boolean;
  /**
   * Daily upload limits (decision R2). `false` only for a testing deploy
   * (`cdk deploy -c dailyLimits=off`); any normal deploy turns them back on.
   * @default true
   */
  readonly dailyLimits?: boolean;
  /**
   * Send photo tiles next to the OCR text (phase 2c). The Pillow layer is always attached;
   * this only sets PHOTO_TILES, so turning it off is a config-only rollback.
   * @default false
   */
  readonly photoTiles?: boolean;
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
    Tags.of(this).add("project", "onebill");


    // Events that fail delivery or keep failing in the Lambda wait here for inspection/replay.
    const deadLetters = new sqs.Queue(this, "ExtractDeadLetters", {
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });

    // Textract announces async job completion here; the message has job and object IDs only.
    const textractDone = new sns.Topic(this, "TextractDoneTopic", { enforceSSL: true });
    const textractPublishRole = new iam.Role(this, "TextractPublishRole", {
      description: "Lets Textract announce async job completion on the OneBill topic",
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
      serviceName: "onebill-documents",
      memorySize: 512,
      // Fits Textract plus the Bedrock budget (2 calls × 2 attempts × 45 s read timeout).
      timeout: Duration.seconds(300),
      deadLetterQueue: deadLetters,
      enableTracing: props.enableTracing,
      layers: [pillowLayer(this)],
      environment: {
        TABLE_NAME: props.table.tableName,
        BUCKET_NAME: props.docsBucket.bucketName,
        BEDROCK_MODEL_ID: props.bedrockModelId,
        PHOTO_TILES: props.photoTiles ? "on" : "off",
        TEXTRACT_TOPIC_ARN: textractDone.topicArn,
        TEXTRACT_ROLE_ARN: textractPublishRole.roleArn,
        ...(props.dailyLimits === false ? { DAILY_LIMITS: "off" } : {}),
      },
    });
    // Uploads are capped at 15 a day for everyone (with limits on); each makes 1–2 calls.
    alarmOver(
      this,
      "ExtractCallsAlarm",
      extractFn.metricInvocations(HOURLY),
      30,
      "Receipt reading ran more than 30 times in an hour: are the daily limits off, or is something looping?",
    );
    alarmOver(
      this,
      "ExtractDeadLettersAlarm",
      deadLetters.metricApproximateNumberOfMessagesVisible({ period: Duration.minutes(5), statistic: "Maximum" }),
      0,
      "A receipt upload failed repeatedly and landed in the dead-letter queue.",
    );
    // All Bedrock output tokens in the account (the metric without dimensions covers every
    // model). A scan is ~2–4k, an email bill at most 1.5k: 60k in an hour is ~$0.33 and unusual.
    alarmOver(
      this,
      "BedrockTokensAlarm",
      new cloudwatch.Metric({ namespace: "AWS/Bedrock", metricName: "OutputTokenCount", ...HOURLY }),
      60_000,
      "Bedrock produced more than 60,000 output tokens in an hour: much more than normal use.",
    );

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
      props.senderEmailParameter ?? "/onebill/sender-email",
    );
    // SES emails a verification link to this address on first deploy; click it once.
    const sender = new ses.EmailIdentity(this, "ReminderSender", { identity: ses.Identity.email(senderEmail) });

    const remindersFn = newServiceFunction(this, "RemindersFunction", {
      handler: "reminders.handler.handler",
      description: "Daily digest of deadlines due in 7, 3, and 1 days",
      serviceName: "onebill-reminders",
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
