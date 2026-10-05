import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { DockerImage, Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";

// Pinned (not "latest") so deploys are reproducible. Source: Powertools install docs.
const POWERTOOLS_LAYER_VERSION = 38;
const LAYER_CACHE = path.join(__dirname, "../.layers");
// public.ecr.aws/sam/build-python3.12:latest-arm64 as of 2026-10-03, pinned by digest.
const SAM_BUILD_IMAGE =
  "public.ecr.aws/sam/build-python3.12@sha256:4eac38937a2ad40a13b81f87cc2b8ffcde46604c83a269f9d912937e6078aea0";

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

/**
 * Pillow for the extract Lambda (photo tiles, phase 2c). Installed from
 * infra/layers/pillow/requirements.txt, which pins every wheel by hash (exported from
 * services/uv.lock), so a tampered download fails the build. Built locally with uv from the
 * manylinux_2_28 arm64 wheel (no Docker), cached by the requirements' hash under infra/.layers.
 * Without uv, CDK builds it in the SAM image, pinned by digest, with the same hashes.
 */
export function pillowLayer(scope: Construct): lambda.ILayerVersion {
  const stack = Stack.of(scope);
  const existing = stack.node.tryFindChild("PillowLayer") as lambda.ILayerVersion | undefined;
  if (existing) return existing;
  const source = path.join(__dirname, "../layers/pillow");
  const requirements = fs.readFileSync(path.join(source, "requirements.txt"), "utf8");
  // The hash of what's installed: a change to the pinned wheels rebuilds and redeploys the layer.
  const digest = createHash("sha256").update(requirements).digest("hex").slice(0, 16);
  return new lambda.LayerVersion(stack, "PillowLayer", {
    description: "Pillow (photo tiles for extraction), hash-pinned",
    compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
    compatibleArchitectures: [lambda.Architecture.ARM_64],
    code: lambda.Code.fromAsset(source, {
      assetHash: `pillow-py312-arm64-${digest}`,
      bundling: {
        image: DockerImage.fromRegistry(SAM_BUILD_IMAGE),
        command: [
          "bash",
          "-c",
          "pip install --require-hashes --only-binary=:all: -r requirements.txt -t /asset-output/python",
        ],
        local: {
          tryBundle(outputDir: string): boolean {
            const cached = path.join(LAYER_CACHE, `pillow-${digest}`);
            try {
              if (!fs.existsSync(path.join(cached, "python", "PIL"))) {
                execFileSync(
                  "uv",
                  [
                    "pip",
                    "install",
                    "--require-hashes",
                    "--python-platform",
                    "aarch64-manylinux_2_28", // Lambda python3.12 = Amazon Linux 2023 (glibc 2.34)
                    "--python-version",
                    "3.12",
                    "--only-binary",
                    ":all:",
                    "--target",
                    path.join(cached, "python"),
                    "-r",
                    path.join(source, "requirements.txt"),
                  ],
                  { stdio: ["ignore", "ignore", "pipe"] },
                );
              }
              fs.cpSync(cached, outputDir, { recursive: true });
              return true;
            } catch (err) {
              // Say why, then let CDK fall back to the pinned build image.
              const detail = err instanceof Error ? err.message : String(err);
              console.warn(`Pillow layer: local uv build failed, using the build image. ${detail}`);
              return false;
            }
          },
        },
      },
    }),
  });
}

export interface ServiceFunctionProps {
  /** Dotted path under services/, e.g. "api.me.handler.handler". */
  readonly handler: string;
  readonly description: string;
  /** Powertools service name, used in structured logs. */
  readonly serviceName: string;
  readonly environment?: Record<string, string>;
  readonly timeout?: Duration;
  readonly memorySize?: number;
  /** Active X-Ray tracing; off by default (cost decision R1). */
  readonly enableTracing?: boolean;
  /** For asynchronously invoked functions: where failed invocations go after retries. */
  readonly deadLetterQueue?: sqs.IQueue;
  /** Extra layers (e.g. Pillow), after Powertools. */
  readonly layers?: lambda.ILayerVersion[];
}

/**
 * A Python 3.12 arm64 Lambda with OneBill defaults: Powertools layer, a log group
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
    layers: [powertoolsLayer(scope), ...(props.layers ?? [])],
    environment: {
      ...props.environment,
      POWERTOOLS_SERVICE_NAME: props.serviceName,
      POWERTOOLS_LOG_LEVEL: "INFO",
      // Don't copy response bodies into X-Ray (it bypasses our PII redaction and log retention).
      POWERTOOLS_TRACER_CAPTURE_RESPONSE: "false",
      // Exception messages can quote document content: keep them out of traces too.
      POWERTOOLS_TRACER_CAPTURE_ERROR: "false",
      POWERTOOLS_TRACE_DISABLED: props.enableTracing ? "false" : "true",
    },
    logGroup: new logs.LogGroup(scope, `${id}Logs`, {
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: RemovalPolicy.DESTROY,
    }),
  });
}
