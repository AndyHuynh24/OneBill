import {
  CfnOutput,
  Duration,
  Stack,
  type StackProps,
  Annotations,
} from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import type { Construct } from "constructs";

export interface WebStackProps extends StackProps {
  /** The built frontend (`frontend/dist`). Left out (tests, or before the first build), the
   *  bucket and distribution are still created, with nothing uploaded. */
  readonly siteDir?: string;
}

/** Files browsers must re-check on every visit: the page, and the PWA's service worker and
 *  manifest (a year-cached service worker would pin people to an old version). */
export const NEVER_CACHED = ["index.html", "sw.js", "manifest.webmanifest"];

/**
 * The public website: the built React app in a private bucket, served over HTTPS by
 * CloudFront. The bucket is never public; CloudFront reads it with Origin Access Control.
 */
export class WebStack extends Stack {
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebStackProps = {}) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
    });

    // Browser security headers on every response. The CSP allows only this site, plus the AWS
    // endpoints the app talks to: the API, Cognito sign-in, and S3 for uploads.
    const headers = new cloudfront.ResponseHeadersPolicy(
      this,
      "SecurityHeaders",
      {
        securityHeadersBehavior: {
          strictTransportSecurity: {
            accessControlMaxAge: Duration.days(365),
            includeSubdomains: true,
            override: true,
          },
          contentTypeOptions: { override: true },
          frameOptions: {
            frameOption: cloudfront.HeadersFrameOption.DENY,
            override: true,
          },
          referrerPolicy: {
            referrerPolicy:
              cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
            override: true,
          },
          contentSecurityPolicy: {
            contentSecurityPolicy: [
              "default-src 'self'",
              "script-src 'self'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self'",
              "connect-src 'self' https://*.amazonaws.com https://*.amazoncognito.com",
              "object-src 'none'",
              "base-uri 'self'",
              "frame-ancestors 'none'",
            ].join("; "),
            override: true,
          },
        },
      },
    );

    this.distribution = new cloudfront.Distribution(this, "Site", {
      comment: "OneBill web app",
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: headers,
        compress: true,
      },
      // A single-page app: /callback and any reloaded page are served index.html. (A private
      // bucket answers 403 for missing files.)
      errorResponses: [403, 404].map((httpStatus) => ({
        httpStatus,
        responseHttpStatus: 200,
        responsePagePath: "/index.html",
        ttl: Duration.seconds(0),
      })),
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
    });

    if (props.siteDir) {
      // Hashed JS/CSS never change, so browsers keep them a year. Old ones stay (no prune), so
      // a tab opened before a deploy keeps working.
      const assets = new s3deploy.BucketDeployment(this, "DeployAssets", {
        sources: [
          s3deploy.Source.asset(props.siteDir, { exclude: NEVER_CACHED }),
        ],
        destinationBucket: bucket,
        prune: false,
        cacheControl: [
          s3deploy.CacheControl.fromString(
            "public, max-age=31536000, immutable",
          ),
        ],
      });
      // The page, service worker, and manifest are always re-checked, so a deploy shows up on
      // the next visit.
      const page = new s3deploy.BucketDeployment(this, "DeployPage", {
        sources: [
          s3deploy.Source.asset(props.siteDir, {
            exclude: ["*", ...NEVER_CACHED.map((file) => `!${file}`)],
          }),
        ],
        destinationBucket: bucket,
        prune: false,
        cacheControl: [s3deploy.CacheControl.fromString("no-cache")],
        distribution: this.distribution,
        distributionPaths: ["/", ...NEVER_CACHED.map((file) => `/${file}`)],
      });
      page.node.addDependency(assets); // the page only goes live once its JS/CSS are there
    } else {
      Annotations.of(this).addWarning(
        "No frontend build: run `make deploy-web` to build and upload it.",
      );
    }

    new CfnOutput(this, "SiteUrl", {
      value: `https://${this.distribution.distributionDomainName}`,
    });
  }
}
