import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, it } from "vitest";
import { AuthStack } from "../lib/auth-stack";

let template: Template;

beforeAll(() => {
  const app = new App();
  const stack = new AuthStack(app, "TestAuth", {
    domainPrefix: "onetally-test",
    frontendUrl: "http://localhost:5173",
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
  it("has an 'uploaders' group that gates paid document processing", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolGroup", {
      GroupName: "uploaders",
      UserPoolId: Match.anyValue(),
    });
  });
});

describe("AuthStack — hosted login", () => {
  it("creates a Cognito prefix domain using managed login", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolDomain", {
      Domain: "onetally-test",
      ManagedLoginVersion: 2,
    });
  });

  it("gives the app client default managed-login branding", () => {
    template.hasResourceProperties("AWS::Cognito::ManagedLoginBranding", {
      UseCognitoProvidedValues: true,
    });
  });
});
