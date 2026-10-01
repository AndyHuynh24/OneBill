import * as path from "node:path";
import { Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";

// Pinned (not "latest") so deploys are reproducible. Source: Powertools install docs.
const POWERTOOLS_LAYER_VERSION = 38;

/**
 * All Python Lambdas ship the whole services/ folder (api/, documents/, reminders/, shared/),
 * so shared code is importable everywhere. Tests and local tooling are excluded.
 * boto3 comes with the Lambda runtime; Powertools comes from the AWS-published layer.
 */
export function servicesCode(): lambda.Code {
  return lambda.Code.fromAsset(path.join(__dirname, "../../services"), {
    exclude: [
      "tests",
      ".venv",
      ".pytest_cache",
      ".ruff_cache",
      "**/__pycache__",
      "**/*.pyc",
      "**/.DS_Store",
      "pyproject.toml",
      "uv.lock",
      ".python-version",
    ],
  });
}

/** One Powertools layer reference per stack. */
function powertoolsLayer(scope: Construct): lambda.ILayerVersion {
  const stack = Stack.of(scope);
  const existing = stack.node.tryFindChild("PowertoolsLayer") as lambda.ILayerVersion | undefined;
  return (
    existing ??
    lambda.LayerVersion.fromLayerVersionArn(
      stack,
      "PowertoolsLayer",
      `arn:aws:lambda:${stack.region}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python312-arm64:${POWERTOOLS_LAYER_VERSION}`,
    )
  );
}

export interface ServiceFunctionProps {
  /** Dotted path under services/, e.g. "api.me.handler.handler". */
  readonly handler: string;
  readonly description: string;
  /** Powertools service name, used in structured logs. */
  readonly serviceName: string;
  /** Encrypts the function's log group. */
  readonly logKey: kms.IKey;
  readonly environment?: Record<string, string>;
  readonly timeout?: Duration;
  readonly memorySize?: number;
  /** Active X-Ray tracing; off by default (cost decision R1). */
  readonly enableTracing?: boolean;
  /** For asynchronously invoked functions: where failed invocations go after retries. */
  readonly deadLetterQueue?: sqs.IQueue;
}

/**
 * A Python 3.12 arm64 Lambda with OneTally defaults: Powertools layer, a KMS-encrypted log group
 * with two-week retention, and no response bodies in traces. Grant permissions explicitly
 * afterwards; this adds none beyond writing its own logs.
 */
export function newServiceFunction(scope: Construct, id: string, props: ServiceFunctionProps): lambda.Function {
  return new lambda.Function(scope, id, {
    description: props.description,
    runtime: lambda.Runtime.PYTHON_3_12,
    architecture: lambda.Architecture.ARM_64,
    code: servicesCode(),
    handler: props.handler,
    memorySize: props.memorySize ?? 256,
    timeout: props.timeout ?? Duration.seconds(10),
    tracing: props.enableTracing ? lambda.Tracing.ACTIVE : undefined,
    deadLetterQueue: props.deadLetterQueue,
    layers: [powertoolsLayer(scope)],
    environment: {
      ...props.environment,
      POWERTOOLS_SERVICE_NAME: props.serviceName,
      POWERTOOLS_LOG_LEVEL: "INFO",
      // Don't copy response bodies into X-Ray (it bypasses our KMS key and PII redaction).
      POWERTOOLS_TRACER_CAPTURE_RESPONSE: "false",
      POWERTOOLS_TRACE_DISABLED: props.enableTracing ? "false" : "true",
    },
    logGroup: new logs.LogGroup(scope, `${id}Logs`, {
      retention: logs.RetentionDays.TWO_WEEKS,
      encryptionKey: props.logKey,
      removalPolicy: RemovalPolicy.DESTROY,
    }),
  });
}
