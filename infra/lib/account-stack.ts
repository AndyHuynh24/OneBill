import { CfnOutput, Duration, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as budgets from "aws-cdk-lib/aws-budgets";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface AccountStackProps extends StackProps {
  /** Where budget alerts go. Passed at deploy time (-c budgetEmail=...), never committed. */
  readonly budgetEmail: string;
  /** "owner/repo" allowed to assume the CI role. */
  readonly githubRepo: string;
}

const GITHUB_OIDC_HOST = "token.actions.githubusercontent.com";

/**
 * Account-wide safety nets, deployed once from a laptop:
 * - a $10/month budget alarm (alerts only; it does not stop spending)
 * - a GitHub Actions role (via OIDC, no stored keys) that can preview infra changes read-only
 */
export class AccountStack extends Stack {
  constructor(scope: Construct, id: string, props: AccountStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onetally");

    const alert = (type: "ACTUAL" | "FORECASTED", threshold: number) => ({
      notification: {
        notificationType: type,
        comparisonOperator: "GREATER_THAN",
        threshold,
        thresholdType: "PERCENTAGE",
      },
      subscribers: [{ subscriptionType: "EMAIL", address: props.budgetEmail }],
    });

    new budgets.CfnBudget(this, "MonthlyBudget", {
      budget: {
        budgetName: "onetally-monthly",
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: 10, unit: "USD" },
      },
      notificationsWithSubscribers: [
        alert("ACTUAL", 20), // $2: early warning, normal spend is ~$1
        alert("ACTUAL", 50),
        alert("ACTUAL", 80),
        alert("ACTUAL", 100),
        alert("FORECASTED", 100),
      ],
    });

    const github = new iam.OidcProviderNative(this, "GitHubOidc", {
      url: `https://${GITHUB_OIDC_HOST}`,
      clientIds: ["sts.amazonaws.com"],
    });

    const ciRole = new iam.Role(this, "CiDiffRole", {
      roleName: "onetally-ci-diff",
      description: "GitHub Actions (pull requests only) runs cdk diff with read-only access",
      maxSessionDuration: Duration.hours(1),
      assumedBy: new iam.OpenIdConnectPrincipal(github, {
        StringEquals: {
          [`${GITHUB_OIDC_HOST}:aud`]: "sts.amazonaws.com",
          [`${GITHUB_OIDC_HOST}:sub`]: `repo:${props.githubRepo}:pull_request`,
        },
      }),
    });

    // `cdk diff --method=template` only needs CDK's bootstrap lookup role (ReadOnlyAccess).
    ciRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [
          `arn:${this.partition}:iam::${this.account}:role/cdk-hnb659fds-lookup-role-${this.account}-${this.region}`,
        ],
      }),
    );

    new CfnOutput(this, "CiRoleArn", { value: ciRole.roleArn });
  }
}
