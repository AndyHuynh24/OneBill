import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpNoneAuthorizer } from "aws-cdk-lib/aws-apigatewayv2";
import { HttpUserPoolAuthorizer } from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { alarmOver, HOURLY } from "./alerts";
import { grantInvokeModel } from "./bedrock";
import { newServiceFunction } from "./python-code";

export interface ApiStackProps extends StackProps {
  readonly table: dynamodb.ITable;
  /** The customer-managed key that encrypts the table; also used for log groups. */
  /** Documents bucket; the API signs browser uploads into its uploads/ prefix. */
  readonly docsBucket: s3.IBucket;
  readonly userPool: cognito.IUserPool;
  readonly userPoolClient: cognito.IUserPoolClient;
  /** Only this browser origin may call the API (CORS). */
  /** Origins allowed to call the API from a browser (the public site and local dev). */
  readonly frontendUrls: string[];
  /**
   * Active X-Ray tracing. Off by default: X-Ray samples every request at ~1/s and can't be
   * turned down, which dominated the abuse-case cost. Enable while debugging.
   * @default false
   */
  readonly enableTracing?: boolean;
  /**
   * Daily upload limits. `false` only for a testing deploy
   * (`cdk deploy -c dailyLimits=off`); any normal deploy turns them back on.
   * @default true
   */
  readonly dailyLimits?: boolean;
  /** Bedrock inference profile ID for reading subscription emails (the ingest Lambda). */
  readonly bedrockModelId: string;
}

/**
 * The front door: an HTTP API whose every route requires a valid Cognito access token,
 * backed by Python Lambdas.
 */
export class ApiStack extends Stack {
  readonly api: apigwv2.HttpApi;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onebill");


    const meFn = newServiceFunction(this, "MeFunction", {
      handler: "api.me.handler.handler",
      description: "GET /me: the caller's PROFILE (created on first call); PATCH /me: settings",
      serviceName: "onebill-api",
      environment: { TABLE_NAME: props.table.tableName },
      enableTracing: props.enableTracing,
    });
    // Least privilege: exactly the calls logic.py makes, on the table only (no Scan, no
    // indexes). UpdateItem: the reminder-email setting. (grantReadWriteData would add
    // Scan/Delete/BatchWrite.)
    meFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
        resources: [props.table.tableArn],
      }),
    );

    const documentsFn = newServiceFunction(this, "DocumentsFunction", {
      handler: "api.documents.handler.handler",
      description: "Documents API: create uploads, list/read documents, list obligations",
      serviceName: "onebill-api",
      environment: {
        TABLE_NAME: props.table.tableName,
        BUCKET_NAME: props.docsBucket.bucketName,
        ...(props.dailyLimits === false ? { DAILY_LIMITS: "off" } : {}),
      },
      enableTracing: props.enableTracing,
    });
    documentsFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Get/Query: read own items. Put: new DOC#, review OBL#. Update: quotas, renewals, review.
        // Delete: obligations replaced during review (inside a transaction).
        actions: [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
        ],
        resources: [props.table.tableArn],
      }),
    );
    documentsFn.addToRolePolicy(
      new iam.PolicyStatement({
        // A presigned POST carries the signer's permissions: the browser can only write under uploads/.
        // DeleteObject: "Delete document" (versioned bucket: adds a delete marker; the old
        // version expires after 30 days).
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [props.docsBucket.arnForObjects("uploads/*")],
      }),
    );

    const authorizer = new HttpUserPoolAuthorizer("CognitoAuthorizer", props.userPool, {
      userPoolClients: [props.userPoolClient],
    });

    this.api = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: "onebill",
      description: "OneBill HTTP API",
      createDefaultStage: false,
      defaultAuthorizer: authorizer,
      // Access tokens carry a `scope` claim; ID tokens don't, so requiring one rejects ID tokens.
      defaultAuthorizationScopes: ["openid"],
      corsPreflight: {
        allowOrigins: props.frontendUrls,
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.PATCH,
          apigwv2.CorsHttpMethod.PUT,
          apigwv2.CorsHttpMethod.DELETE,
        ],
        allowHeaders: ["authorization", "content-type"],
        maxAge: Duration.hours(1),
      },
    });

    // Explicit $default stage so we can set throttling (cost/abuse guard) and access logs.
    const accessLogs = new logs.LogGroup(this, "ApiAccessLogs", {
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const stage = new apigwv2.HttpStage(this, "DefaultStage", {
      httpApi: this.api,
      stageName: "$default",
      autoDeploy: true,
      // Low on purpose: plenty for one person, and it caps the worst-case bill
      // if someone abuses an open-sign-up account (~$8/month at this limit).
      throttle: { rateLimit: 2, burstLimit: 5 },
      accessLogSettings: {
        destination: new apigwv2.LogGroupLogDestination(accessLogs),
        // No IPs or headers: keep personal data out of logs.
        format: apigw.AccessLogFormat.custom(
          JSON.stringify({
            requestId: "$context.requestId",
            time: "$context.requestTime",
            routeKey: "$context.routeKey",
            status: "$context.status",
            latencyMs: "$context.responseLatency",
            authorizerError: "$context.authorizer.error",
            integrationError: "$context.integrationErrorMessage",
          }),
        ),
      },
    });

    this.api.addRoutes({
      path: "/me",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.PATCH],
      integration: new HttpLambdaIntegration("MeIntegration", meFn),
    });
    const documents = new HttpLambdaIntegration("DocumentsIntegration", documentsFn);
    this.api.addRoutes({
      path: "/documents",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
      integration: documents,
    });
    this.api.addRoutes({
      path: "/documents/{docId}",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.PATCH, apigwv2.HttpMethod.DELETE],
      integration: documents,
    });
    this.api.addRoutes({ path: "/obligations", methods: [apigwv2.HttpMethod.GET], integration: documents });
    // Split bill: served by the same Lambda; no new function or permissions needed.
    this.api.addRoutes({
      path: "/documents/{docId}/split",
      methods: [apigwv2.HttpMethod.PUT],
      integration: documents,
    });
    this.api.addRoutes({
      path: "/documents/{docId}/split/paid",
      methods: [apigwv2.HttpMethod.PUT],
      integration: documents,
    });


    // Subscriptions and the Gmail connection (signed in): same Lambda, no new permissions.
    this.api.addRoutes({
      path: "/subscriptions",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
      integration: documents,
    });
    this.api.addRoutes({
      path: "/subscriptions/{key}",
      methods: [apigwv2.HttpMethod.PATCH, apigwv2.HttpMethod.DELETE],
      integration: documents,
    });
    this.api.addRoutes({ path: "/obligations/{oblId}", methods: [apigwv2.HttpMethod.DELETE], integration: documents });
    // Renew today's scans once they're used up (twice a month; never the owner's).
    this.api.addRoutes({ path: "/usage/renew", methods: [apigwv2.HttpMethod.POST], integration: documents });
    this.api.addRoutes({
      path: "/ingest/keys",
      methods: [apigwv2.HttpMethod.POST, apigwv2.HttpMethod.DELETE],
      integration: documents,
    });

    // Gmail script → POST /ingest/email. Apps Script can't sign in with Cognito, so this is the
    // one route without the JWT authorizer: the Lambda looks up the hashed ingest key and takes
    // the user only from that record. The stage throttle and a daily email cap still apply.
    const ingestFn = newServiceFunction(this, "IngestFunction", {
      handler: "api.ingest.handler.handler",
      description: "Gmail ingest: verify key, read subscription email with Bedrock, save SUB#/OBL#",
      serviceName: "onebill-ingest",
      environment: { TABLE_NAME: props.table.tableName, BEDROCK_MODEL_ID: props.bedrockModelId },
      timeout: Duration.seconds(29), // API Gateway ends the request at 30 s
      enableTracing: props.enableTracing,
    });
    ingestFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Get: key lookup, SUB#. Put: EMAIL# marker, SUB#/OBL# (in a transaction).
        // Update: daily cap, marker outcome, last-used. Delete: a replaced renewal reminder.
        actions: ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
        resources: [props.table.tableArn],
      }),
    );
    grantInvokeModel(ingestFn, props.bedrockModelId);
    const [ingestRoute] = this.api.addRoutes({
      path: "/ingest/email",
      methods: [apigwv2.HttpMethod.POST],
      integration: new HttpLambdaIntegration("IngestIntegration", ingestFn),
      authorizer: new HttpNoneAuthorizer(),
      authorizationScopes: [], // don't inherit the API's default "openid" scope (JWT-only)
    });
    // The open route gets its own, much tighter throttle (cost review): the script sends emails
    // one at a time, so 1 every 5 s is plenty, and an anonymous flood costs ~10× less.
    const cfnStage = stage.node.defaultChild as apigwv2.CfnStage;
    cfnStage.routeSettings = {
      "POST /ingest/email": { ThrottlingRateLimit: 0.2, ThrottlingBurstLimit: 2 },
    };
    cfnStage.addDependency(ingestRoute.node.defaultChild as apigwv2.CfnRoute);
    // Normal use is a few emails a day; the route allows at most ~720 calls an hour.
    alarmOver(
      this,
      "IngestCallsAlarm",
      ingestFn.metricInvocations(HOURLY),
      60,
      "Gmail ingest called more than 60 times in an hour: a looping script or a leaked key?",
    );

    new CfnOutput(this, "ApiUrl", { value: stage.url });
  }
}
