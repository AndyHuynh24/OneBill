import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { NEVER_CACHED, WebStack } from "../lib/web-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  template = Template.fromStack(
    new WebStack(app, "TestWeb", {
      env: { account: "123456789012", region: "us-east-1" },
    }),
  );
});

describe("WebStack", () => {
  it("keeps the site bucket private and HTTPS-only", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
    const policy = Object.values(
      template.findResources("AWS::S3::BucketPolicy"),
    )[0];
    expect(JSON.stringify(policy)).toContain("aws:SecureTransport");
  });

  it("CloudFront reads the bucket with Origin Access Control (not a public website)", () => {
    template.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
    const policy = JSON.stringify(
      Object.values(template.findResources("AWS::S3::BucketPolicy"))[0],
    );
    expect(policy).toContain("cloudfront.amazonaws.com");
    expect(policy).toContain("AWS:SourceArn");
  });

  it("redirects to HTTPS and serves index.html for app routes like /callback", () => {
    template.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        DefaultRootObject: "index.html",
        DefaultCacheBehavior: Match.objectLike({
          ViewerProtocolPolicy: "redirect-to-https",
        }),
        CustomErrorResponses: [
          {
            ErrorCode: 403,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
            ErrorCachingMinTTL: 0,
          },
          {
            ErrorCode: 404,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
            ErrorCachingMinTTL: 0,
          },
        ],
      }),
    });
  });

  it("sends security headers with a CSP that allows only this site and AWS endpoints", () => {
    const policy = Object.values(
      template.findResources("AWS::CloudFront::ResponseHeadersPolicy"),
    )[0];
    const security =
      policy.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig;
    expect(security.StrictTransportSecurity.AccessControlMaxAgeSec).toBe(
      31536000,
    );
    expect(security.FrameOptions.FrameOption).toBe("DENY");
    const csp: string = security.ContentSecurityPolicy.ContentSecurityPolicy;
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("unsafe-eval");
  });

  it("outputs the site's HTTPS address", () => {
    template.hasOutput("SiteUrl", {
      Value: Match.objectLike({ "Fn::Join": Match.anyValue() }),
    });
  });

  it("never caches the page, the service worker, or the app manifest", () => {
    expect(NEVER_CACHED).toEqual(
      expect.arrayContaining(["index.html", "sw.js", "manifest.webmanifest"]),
    );
  });
});
