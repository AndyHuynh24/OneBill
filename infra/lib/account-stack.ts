import { CfnOutput, Duration, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as budgets from "aws-cdk-lib/aws-budgets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import { ALERTS_TOPIC_NAME } from "./alerts";
import { Construct } from "constructs";

export interface AccountStackProps extends StackProps {
  /** Where budget alerts go. Passed at deploy time (-c budgetEmail=...), never committed. */
  readonly budgetEmail: string;
  /**
   * GitHub's OIDC subject for the repo allowed to assume the CI role. This repo uses GitHub's
   * immutable format, "repo:<owner>@<ownerId>/<repo>@<repoId>": a deleted and recreated repo with
   * the same name gets a new ID and can't match. Check it with
   * `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` (sub_claim_prefix).
   */
  readonly githubSubject: string;
  /**
   * Create the GitHub Actions role for `cdk diff` on pull requests. Off by default (2026-10-04):
   * with no customer-managed key, a read-only role could read the table, so CI isn't connected
   * to AWS. Turn on only together with a customer-managed key (`-c ciRole=on`).
   */
  readonly ciRole?: boolean;
}

const GITHUB_OIDC_HOST = "token.actions.githubusercontent.com";

/**
 * Account-wide safety nets, deployed once from a laptop:
 * - a $10/month budget alarm (alerts only; it does not stop spending)
 * - an alerts topic, emailing the same address, that the app stacks' alarms publish to
 * - optionally, a GitHub Actions role (via OIDC, no stored keys) to preview infra changes
 */
export class AccountStack extends Stack {
  constructor(scope: Construct, id: string, props: AccountStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onebill");

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
        // No fixed budgetName: changing the alert list replaces the budget, and a fixed name
        // makes the replacement collide with the old one. CloudFormation generates a unique name.
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: 10, unit: "USD" },
      },
      notificationsWithSubscribers: [
        alert("ACTUAL", 30), // $3: early warning; normal spend is ~$2 (decision R3)
        alert("ACTUAL", 50),
        alert("ACTUAL", 80),
        alert("ACTUAL", 100),
        alert("FORECASTED", 100),
      ],
    });

    // Alarm notifications carry only metric names and numbers: no document data.
    const alerts = new sns.Topic(this, "AlertsTopic", {
      topicName: ALERTS_TOPIC_NAME,
      displayName: "OneBill alerts",
      enforceSSL: true,
    });
    alerts.addSubscription(new subscriptions.EmailSubscription(props.budgetEmail));

    if (!props.ciRole) return;

    const github = new iam.OidcProviderNative(this, "GitHubOidc", {
      url: `https://${GITHUB_OIDC_HOST}`,
      clientIds: ["sts.amazonaws.com"],
    });

    const ciRole = new iam.Role(this, "CiDiffRole", {
      roleName: "onebill-ci-diff",
      description: "GitHub Actions (pull requests only) runs cdk diff with read-only access",
      maxSessionDuration: Duration.hours(1),
      assumedBy: new iam.OpenIdConnectPrincipal(github, {
        StringEquals: {
          [`${GITHUB_OIDC_HOST}:aud`]: "sts.amazonaws.com",
          [`${GITHUB_OIDC_HOST}:sub`]: `${props.githubSubject}:pull_request`,
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
