import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, it } from "vitest";
import { AuthStack } from "../lib/auth-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  const stack = new AuthStack(app, "TestAuth", {
    domainPrefix: "onebill-test",
    frontendUrls: ["http://localhost:5173"],
  });
  template = Template.fromStack(stack);
});

describe("AuthStack — user pool", () => {
  it("allows self sign-up with email sign-in and email verification", () => {
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: false },
      UsernameAttributes: ["email"],
      AutoVerifiedAttributes: ["email"],
    });
  });

  it("uses the Essentials plan, optional TOTP MFA, and a 12+ char password policy", () => {
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      UserPoolTier: "ESSENTIALS",
      MfaConfiguration: "OPTIONAL",
      EnabledMfas: ["SOFTWARE_TOKEN_MFA"],
      Policies: { PasswordPolicy: Match.objectLike({ MinimumLength: 12 }) },
    });
  });

  it("has deletion protection and is retained", () => {
    template.hasResource("AWS::Cognito::UserPool", {
      Properties: Match.objectLike({ DeletionProtection: "ACTIVE" }),
      DeletionPolicy: "Retain",
    });
  });
});

describe("AuthStack — app client", () => {
  it("is a public client using only the authorization-code grant", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      GenerateSecret: false,
      AllowedOAuthFlows: ["code"],
      AllowedOAuthFlowsUserPoolClient: true,
      AllowedOAuthScopes: ["openid", "email", "profile"],
      SupportedIdentityProviders: ["COGNITO"],
      PreventUserExistenceErrors: "ENABLED",
    });
  });

  it("only redirects to the registered frontend URLs", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      CallbackURLs: ["http://localhost:5173/callback"],
      LogoutURLs: ["http://localhost:5173/"],
    });
  });
});

describe("AuthStack — upload access", () => {
  it("has an 'owners' group for the app owner's own upload cap and Connect Gmail", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolGroup", { GroupName: "owners" });
    template.resourceCountIs("AWS::Cognito::UserPoolGroup", 1);
  });
});

describe("AuthStack — hosted login", () => {
  it("creates a Cognito prefix domain using managed login", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolDomain", {
      Domain: "onebill-test",
      ManagedLoginVersion: 2,
    });
  });

});

describe("AuthStack — sign in with Google (phase 2e)", () => {
  const withGoogle = () => {
    const app = new App();
    const stack = new AuthStack(app, "TestAuthGoogle", {
      domainPrefix: "x",
      frontendUrls: ["http://localhost:5173"],
      googleClientId: "123.apps.googleusercontent.com",
    });
    return Template.fromStack(stack);
  };

  it("adds Google with its email marked verified, the secret from Secrets Manager", () => {
    const t = withGoogle();
    t.hasResourceProperties("AWS::Cognito::UserPoolIdentityProvider", {
      ProviderName: "Google",
      ProviderType: "Google",
      ProviderDetails: Match.objectLike({
        client_id: "123.apps.googleusercontent.com",
        client_secret: Match.stringLikeRegexp("resolve:secretsmanager:onebill/google-oauth:SecretString:client_secret"),
        authorize_scopes: "openid email profile",
      }),
      AttributeMapping: Match.objectLike({ email: "email", email_verified: "email_verified" }),
    });
  });

  it("offers Google next to email on the web client", () => {
    withGoogle().hasResourceProperties("AWS::Cognito::UserPoolClient", {
      SupportedIdentityProviders: ["COGNITO", "Google"],
    });
  });

  it("without a client ID, sign-in stays email only", () => {
    template.resourceCountIs("AWS::Cognito::UserPoolIdentityProvider", 0);
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", { SupportedIdentityProviders: ["COGNITO"] });
  });
});

describe("AuthStack — login page branding", () => {
  it("shows OneBill's colors and logo on Cognito's login page", () => {
    template.hasResourceProperties("AWS::Cognito::ManagedLoginBranding", {
      UseCognitoProvidedValues: false,
      Settings: Match.objectLike({
        components: Match.objectLike({
          primaryButton: { lightMode: Match.objectLike({ defaults: { backgroundColor: "2b7a69ff", textColor: "ffffffff" } }) },
          form: Match.objectLike({ logo: Match.objectLike({ enabled: true }) }),
        }),
      }),
      Assets: [Match.objectLike({ Category: "FORM_LOGO", ColorMode: "LIGHT", Extension: "SVG" })],
    });
  });
});

