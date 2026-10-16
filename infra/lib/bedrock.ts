import { Stack } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";

/** Amazon Titan Text Embeddings V2: turns a bill's text (or a question) into a vector. */
export const EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0";

/** Let a function create embeddings with Titan V2 (called directly, not through a profile). */
export function grantEmbeddings(fn: lambda.IFunction): void {
  const stack = Stack.of(fn);
  fn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [`arn:${stack.partition}:bedrock:${stack.region}::foundation-model/${EMBED_MODEL_ID}`],
    }),
  );
}

/**
 * Let a function call exactly one Bedrock model through its cross-region inference profile.
 * The profile routes to the model in several US regions: allow the profile itself, and the
 * model only when invoked through that profile.
 */
export function grantInvokeModel(fn: lambda.IFunction, modelId: string): void {
  const stack = Stack.of(fn);
  const profileArn = `arn:${stack.partition}:bedrock:${stack.region}:${stack.account}:inference-profile/${modelId}`;
  const foundationModelId = modelId.replace(/^[a-z]{2,4}\./, "");
  fn.addToRolePolicy(new iam.PolicyStatement({ actions: ["bedrock:InvokeModel"], resources: [profileArn] }));
  fn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [`arn:${stack.partition}:bedrock:*::foundation-model/${foundationModelId}`],
      conditions: { StringEquals: { "bedrock:InferenceProfileArn": profileArn } },
    }),
  );
}
