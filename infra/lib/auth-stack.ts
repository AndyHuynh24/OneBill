import { CfnOutput, RemovalPolicy, SecretValue, Stack, StackProps, Tags } from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import { Construct } from "constructs";
import { loginBrandingAssets, loginBrandingSettings } from "./branding";

export interface AuthStackProps extends StackProps {
  /** Globally unique prefix for the hosted login URL (<prefix>.auth.<region>.amazoncognito.com). */
  readonly domainPrefix: string;
  /** Where the React app runs; Cognito only redirects to URLs registered here. */
  /** Every address the app runs at (the public site and local dev): sign-in may return to each. */
  readonly frontendUrls: string[];
  /**
   * Google OAuth client ID ("Continue with Google"). Not a
   * secret. Without it, sign-in is email only.
   */
  readonly googleClientId?: string;
  /** Secrets Manager secret holding {"client_secret": "..."} for that client. */
  readonly googleSecretName?: string;
}

/**
 * Who the users are: a Cognito user pool, the app client the React app logs in with,
 * and the hosted (managed) login page.
 */
export class AuthStack extends Stack {
  readonly userPool: cognito.UserPool;
  readonly client: cognito.UserPoolClient;
  readonly domain: cognito.UserPoolDomain;
  /**
   * The app owner (added by hand): 25 uploads a day outside the shared cap, and Connect Gmail.
   * Everyone else signed in gets 3 uploads a day.
   */
  readonly ownersGroup: cognito.UserPoolGroup;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);
    Tags.of(this).add("project", "onebill");

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

    this.ownersGroup = new cognito.UserPoolGroup(this, "OwnersGroup", {
      userPool: this.userPool,
      groupName: "owners",
      description: "The app owner: a higher daily upload cap of their own, and Connect Gmail",
    });

    // Sign in with Google, next to email. The client secret stays in Secrets Manager; CloudFormation
    // resolves it at deploy time, so it's never in code, git, or the template.
    const providers = [cognito.UserPoolClientIdentityProvider.COGNITO];
    let google: cognito.UserPoolIdentityProviderGoogle | undefined;
    if (props.googleClientId) {
      google = new cognito.UserPoolIdentityProviderGoogle(this, "Google", {
        userPool: this.userPool,
        clientId: props.googleClientId,
        clientSecretValue: SecretValue.secretsManager(props.googleSecretName ?? "onebill/google-oauth", {
          jsonField: "client_secret",
        }),
        scopes: ["openid", "email", "profile"],
        attributeMapping: {
          email: cognito.ProviderAttribute.GOOGLE_EMAIL,
          // Reminders only go to verified addresses: carry Google's flag over.
          emailVerified: cognito.ProviderAttribute.GOOGLE_EMAIL_VERIFIED,
          givenName: cognito.ProviderAttribute.GOOGLE_GIVEN_NAME,
          familyName: cognito.ProviderAttribute.GOOGLE_FAMILY_NAME,
        },
      });
      providers.push(cognito.UserPoolClientIdentityProvider.GOOGLE);
    }

    // Public client (no secret): code lives in the browser, which can't keep secrets. PKCE protects the code exchange.
    this.client = this.userPool.addClient("WebClient", {
      generateSecret: false,
      authFlows: {},
      preventUserExistenceErrors: true,
      supportedIdentityProviders: providers,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.PROFILE,
        ],
        callbackUrls: props.frontendUrls.map((url) => `${url}/callback`),
        logoutUrls: props.frontendUrls.map((url) => `${url}/`),
      },
    });

    if (google) this.client.node.addDependency(google); // the provider must exist first

    this.domain = this.userPool.addDomain("HostedDomain", {
      cognitoDomain: { domainPrefix: props.domainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });

    // Managed login needs a branding record per client: OneBill's colors and logo (lib/branding.ts).
    new cognito.CfnManagedLoginBranding(this, "WebClientBranding", {
      userPoolId: this.userPool.userPoolId,
      clientId: this.client.userPoolClientId,
      useCognitoProvidedValues: false,
      settings: loginBrandingSettings,
      assets: loginBrandingAssets,
    });

    // Outputs feed frontend/.env.local (make frontend-env). None of these are secrets.
    new CfnOutput(this, "UserPoolId", { value: this.userPool.userPoolId });
    new CfnOutput(this, "UserPoolClientId", { value: this.client.userPoolClientId });
    new CfnOutput(this, "HostedDomain", { value: this.domain.baseUrl().replace("https://", "") });
  }
}
