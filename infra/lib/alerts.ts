import { Duration, Stack } from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import { Construct } from "constructs";

/**
 * Alarms email the same address as the $10 budget: the topic lives in the account stack (made
 * with -c budgetEmail=..., never committed), and app stacks find it by name. Alarms catch abuse
 * within the hour, sooner than budget emails (cost review). Standard alarms: the first 10 are free.
 */
export const ALERTS_TOPIC_NAME = "onetally-alerts";

export function alertsTopic(scope: Construct): sns.ITopic {
  const stack = Stack.of(scope);
  const existing = stack.node.tryFindChild("AlertsTopic") as sns.ITopic | undefined;
  return (
    existing ??
    sns.Topic.fromTopicArn(
      stack,
      "AlertsTopic",
      `arn:${stack.partition}:sns:${stack.region}:${stack.account}:${ALERTS_TOPIC_NAME}`,
    )
  );
}

/** An alarm when `metric` (summed per hour) goes over `threshold`, emailing the alerts topic. */
export function alarmOver(
  scope: Construct,
  id: string,
  metric: cloudwatch.IMetric,
  threshold: number,
  description: string,
): cloudwatch.Alarm {
  const alarm = new cloudwatch.Alarm(scope, id, {
    metric,
    threshold,
    evaluationPeriods: 1,
    comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    alarmDescription: description,
  });
  alarm.addAlarmAction(new actions.SnsAction(alertsTopic(scope)));
  return alarm;
}

export const HOURLY = { period: Duration.hours(1), statistic: "Sum" } as const;
