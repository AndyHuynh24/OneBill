import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { AccountStack } from "../lib/account-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  const stack = new AccountStack(app, "TestAccount", {
    env: { account: "123456789012", region: "us-east-1" },
    budgetEmail: "alerts@example.com",
    githubRepo: "AndyHuynh24/onetally",
  });
  template = Template.fromStack(stack);
});

describe("AccountStack — budget", () => {
  it("creates a $10 monthly cost budget", () => {
    template.hasResourceProperties("AWS::Budgets::Budget", {
      Budget: Match.objectLike({
        BudgetType: "COST",
        TimeUnit: "MONTHLY",
        BudgetLimit: { Amount: 10, Unit: "USD" },
      }),
    });
  });

  it("lets CloudFormation name the budget, so alert changes (a replacement) don't collide", () => {
    const budget = Object.values(template.findResources("AWS::Budgets::Budget"))[0];
    expect(budget.Properties.Budget.BudgetName).toBeUndefined();
  });

  it("emails at 30/50/80/100% actual and 100% forecasted", () => {
    const budget = Object.values(template.findResources("AWS::Budgets::Budget"))[0];
    const notifications = budget.Properties.NotificationsWithSubscribers.map(
      (n: { Notification: { NotificationType: string; Threshold: number } }) =>
        `${n.Notification.NotificationType}:${n.Notification.Threshold}`,
    );
    expect(notifications.sort()).toEqual(["ACTUAL:100", "ACTUAL:30", "ACTUAL:50", "ACTUAL:80", "FORECASTED:100"]);
    for (const n of budget.Properties.NotificationsWithSubscribers) {
      expect(n.Subscribers).toEqual([{ SubscriptionType: "EMAIL", Address: "alerts@example.com" }]);
    }
  });
});

describe("AccountStack — GitHub OIDC CI role", () => {
  it("registers GitHub's OIDC provider with the STS audience", () => {
    template.hasResourceProperties("AWS::IAM::OIDCProvider", {
      Url: "https://token.actions.githubusercontent.com",
      ClientIdList: ["sts.amazonaws.com"],
    });
  });

  it("only trusts pull_request workflows from this repo", () => {
    template.hasResourceProperties("AWS::IAM::Role", {
      RoleName: "onetally-ci-diff",
      MaxSessionDuration: 3600,
      AssumeRolePolicyDocument: {
        Statement: [
          Match.objectLike({
            Action: "sts:AssumeRoleWithWebIdentity",
            Effect: "Allow",
            Condition: {
              StringEquals: {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:sub": "repo:AndyHuynh24/onetally:pull_request",
              },
            },
          }),
        ],
      },
    });
  });

  it("may only assume the CDK bootstrap lookup role (read-only)", () => {
    const policies = Object.values(template.findResources("AWS::IAM::Policy"));
    const statements = policies.flatMap((p) => p.Properties.PolicyDocument.Statement);
    expect(statements).toHaveLength(1);
    expect(statements[0].Action).toBe("sts:AssumeRole");
    expect(JSON.stringify(statements[0].Resource)).toContain("cdk-hnb659fds-lookup-role-");
    expect(JSON.stringify(statements[0].Resource)).not.toContain("deploy-role");
  });
});
