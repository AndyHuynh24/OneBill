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
    githubSubject: "repo:AndyHuynh24@241491614/onebill@1398902241",
    ciRole: true, // the opt-in path: the CI tests below
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

  it("only trusts pull_request workflows from this exact repo (immutable owner and repo IDs)", () => {
    template.hasResourceProperties("AWS::IAM::Role", {
      RoleName: "onebill-ci-diff",
      MaxSessionDuration: 3600,
      AssumeRolePolicyDocument: {
        Statement: [
          Match.objectLike({
            Action: "sts:AssumeRoleWithWebIdentity",
            Effect: "Allow",
            Condition: {
              StringEquals: {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                // A deleted-and-recreated repo with the same name gets a new ID, so it can't match.
                "token.actions.githubusercontent.com:sub":
                  "repo:AndyHuynh24@241491614/onebill@1398902241:pull_request",
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

describe("AccountStack — alerts topic", () => {
  it("has the alerts topic, emailing the budget address, and no CI role unless asked", () => {
    const app = new App();
    const stack = new AccountStack(app, "TestAccountAlerts", {
      budgetEmail: "me@example.com",
      githubSubject: "repo:owner@1/repo@2",
      env: { account: "123456789012", region: "us-east-1" },
    });
    const template = Template.fromStack(stack);
    template.hasResourceProperties("AWS::SNS::Topic", { TopicName: "onebill-alerts" });
    template.hasResourceProperties("AWS::SNS::Subscription", { Protocol: "email", Endpoint: "me@example.com" });
    // By default (no customer-managed key), CI isn't connected to AWS: no role, no OIDC login.
    template.resourceCountIs("AWS::IAM::Role", 0);
    template.resourceCountIs("AWS::IAM::OIDCProvider", 0);
  });
});
