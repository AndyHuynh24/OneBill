import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildGmailScript, GMAIL_MANIFEST, GMAIL_SCRIPT_TEMPLATE } from "./gmailScript";

const repoFile = (name: string) => readFileSync(resolve(__dirname, "../../integrations/gmail-apps-script", name), "utf8");

describe("the ready-made Gmail script", () => {
  it("is the repo's script with your API address and key filled in", () => {
    const script = buildGmailScript("https://api.example.com/", "otk_abc");

    expect(script).toContain("var ONETALLY_URL = 'https://api.example.com/';");
    expect(script).toContain("var ONETALLY_KEY = 'otk_abc';");
    expect(script).not.toContain("__ONETALLY_");
  });

  it("stays valid JavaScript whatever the values contain", () => {
    const script = buildGmailScript("https://x.example/it's$&", "otk_\\'$'");

    expect(() => new Function(script)).not.toThrow();
    expect(script).toContain("var ONETALLY_URL = 'https://x.example/it\\'s$&';");
  });

  it("matches the files in integrations/gmail-apps-script (one source of truth)", () => {
    expect(GMAIL_SCRIPT_TEMPLATE.trim()).toBe(repoFile("Code.gs").trim());
    expect(JSON.parse(GMAIL_MANIFEST)).toEqual(JSON.parse(repoFile("appsscript.json")));
  });

  it("asks Google only for read-only mail access", () => {
    expect(JSON.parse(GMAIL_MANIFEST).oauthScopes).toContain("https://www.googleapis.com/auth/gmail.readonly");
    expect(GMAIL_MANIFEST).not.toContain("https://mail.google.com/");
  });
});
