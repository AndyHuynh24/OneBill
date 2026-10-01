import { CfnOutput, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import { Construct } from "constructs";

export interface AuthStackProps extends StackProps {
  /** Globally unique prefix for the hosted login URL (<prefix>.auth.<region>.amazoncognito.com). */
  readonly domainPrefix: string;
  /** Where the React app runs; Cognito only redirects to URLs registered here. */
  readonly frontendUrl: string;
}

/**
 * Who the users are: a Cognito user pool, the app client the React app logs in with,
 * and the hosted (managed) login page.
 */
export class AuthStack extends Stack {
  readonly userPool: cognito.UserPool;
  readonly client: cognito.UserPoolClient;
  readonly domain: cognito.UserPoolDomain;
  /** Members may upload documents (paid Textract/Bedrock calls). Added by hand: decision D5. */
  readonly uploadersGroup: cognito.UserPoolGroup;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onetally");

    this.userPool = new cognito.UserPool(this, "UserPool", {
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      signInCaseSensitive: false,
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { otp: true, sms: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.uploadersGroup = new cognito.UserPoolGroup(this, "UploadersGroup", {
      userPool: this.userPool,
      groupName: "uploaders",
      description: "May upload documents for OCR and AI extraction",
    });

    // Public client (no secret): code lives in the browser, which can't keep secrets. PKCE protects the code exchange.
    this.client = this.userPool.addClient("WebClient", {
      generateSecret: false,
      authFlows: {},
      preventUserExistenceErrors: true,
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.PROFILE,
        ],
        callbackUrls: [`${props.frontendUrl}/callback`],
        logoutUrls: [`${props.frontendUrl}/`],
      },
    });

    this.domain = this.userPool.addDomain("HostedDomain", {
      cognitoDomain: { domainPrefix: props.domainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });

    // Managed login needs a branding record per client; use Cognito's default look for now.
    new cognito.CfnManagedLoginBranding(this, "WebClientBranding", {
      userPoolId: this.userPool.userPoolId,
      clientId: this.client.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    // Outputs feed frontend/.env.local (make frontend-env). None of these are secrets.
    new CfnOutput(this, "UserPoolId", { value: this.userPool.userPoolId });
    new CfnOutput(this, "UserPoolClientId", { value: this.client.userPoolClientId });
    new CfnOutput(this, "HostedDomain", { value: this.domain.baseUrl().replace("https://", "") });
  }
}
