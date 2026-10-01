import * as path from "node:path";
import * as lambda from "aws-cdk-lib/aws-lambda";

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
