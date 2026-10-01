import * as path from "node:path";
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpUserPoolAuthorizer } from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";

// Pinned (not "latest") so deploys are reproducible. Source: Powertools install docs.
const POWERTOOLS_LAYER_VERSION = 38;

export interface ApiStackProps extends StackProps {
  readonly table: dynamodb.ITable;
  /** The customer-managed key that encrypts the table; also used for log groups. */
  readonly dataKey: kms.IKey;
  readonly userPool: cognito.IUserPool;
  readonly userPoolClient: cognito.IUserPoolClient;
  /** Only this browser origin may call the API (CORS). */
  readonly frontendUrl: string;
  /**
   * Active X-Ray tracing. Off by default: X-Ray samples every request at ~1/s and can't be
   * turned down, which dominated the abuse-case cost (decision R1). Enable while debugging.
   * @default false
   */
  readonly enableTracing?: boolean;
}

/**
 * The front door: an HTTP API whose every route requires a valid Cognito access token,
 * backed by Python Lambdas.
 */
export class ApiStack extends Stack {
  readonly api: apigwv2.HttpApi;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onetally");

    const powertools = lambda.LayerVersion.fromLayerVersionArn(
      this,
      "PowertoolsLayer",
      `arn:aws:lambda:${this.region}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python312-arm64:${POWERTOOLS_LAYER_VERSION}`,
    );

    const meFn = new lambda.Function(this, "MeFunction", {
      description: "GET /me: return the caller's PROFILE item, creating it on first call",
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      // The asset is the source folder only; boto3 comes with the runtime, Powertools with the layer.
      code: lambda.Code.fromAsset(path.join(__dirname, "../../services/api"), {
        exclude: ["**/__pycache__/**", "**/*.pyc"],
      }),
      handler: "me.handler.handler",
      memorySize: 256,
      timeout: Duration.seconds(10),
      tracing: props.enableTracing ? lambda.Tracing.ACTIVE : undefined,
      layers: [powertools],
      environment: {
        TABLE_NAME: props.table.tableName,
        POWERTOOLS_SERVICE_NAME: "onetally-api",
        POWERTOOLS_LOG_LEVEL: "INFO",
        // Don't copy response bodies into X-Ray (it bypasses our KMS key and PII redaction).
        POWERTOOLS_TRACER_CAPTURE_RESPONSE: "false",
        POWERTOOLS_TRACE_DISABLED: props.enableTracing ? "false" : "true",
      },
      logGroup: new logs.LogGroup(this, "MeFunctionLogs", {
        retention: logs.RetentionDays.TWO_WEEKS,
        encryptionKey: props.dataKey,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });

    // Least privilege: exactly the two calls logic.py makes, on the table only (no Scan, no
    // indexes), plus use of the table's key only when DynamoDB calls KMS on our behalf.
    // (grantReadWriteData would add Scan/Delete/BatchWrite and wildcard KMS actions.)
    meFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:PutItem"],
        resources: [props.table.tableArn],
      }),
    );
    meFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
        resources: [props.dataKey.keyArn],
        conditions: { StringEquals: { "kms:ViaService": `dynamodb.${this.region}.amazonaws.com` } },
      }),
    );

    const authorizer = new HttpUserPoolAuthorizer("CognitoAuthorizer", props.userPool, {
      userPoolClients: [props.userPoolClient],
    });

    this.api = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: "onetally",
      description: "OneTally HTTP API",
      createDefaultStage: false,
      defaultAuthorizer: authorizer,
      // Access tokens carry a `scope` claim; ID tokens don't, so requiring one rejects ID tokens.
      defaultAuthorizationScopes: ["openid"],
      corsPreflight: {
        allowOrigins: [props.frontendUrl],
        allowMethods: [apigwv2.CorsHttpMethod.GET],
        allowHeaders: ["authorization", "content-type"],
        maxAge: Duration.hours(1),
      },
    });

    // Explicit $default stage so we can set throttling (cost/abuse guard) and access logs.
    const accessLogs = new logs.LogGroup(this, "ApiAccessLogs", {
      retention: logs.RetentionDays.TWO_WEEKS,
      encryptionKey: props.dataKey,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const stage = new apigwv2.HttpStage(this, "DefaultStage", {
      httpApi: this.api,
      stageName: "$default",
      autoDeploy: true,
      // Low on purpose (decision R1): plenty for one person, and it caps the worst-case bill
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
      methods: [apigwv2.HttpMethod.GET],
      integration: new HttpLambdaIntegration("MeIntegration", meFn),
    });

    new CfnOutput(this, "ApiUrl", { value: stage.url });
  }
}
