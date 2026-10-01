/**
 * Origins the web app may run on: the main frontend URL plus optional dev origins, e.g. the
 * dev server opened from a phone on the same Wi-Fi (`make dev-lan`). Cognito callbacks, API CORS
 * and S3 upload CORS all use this same list.
 */
export function appOrigins(frontendUrl: string, devOrigins: string[] = []): string[] {
  for (const origin of devOrigins) {
    let url: URL;
    try {
      url = new URL(origin);
    } catch {
      throw new Error(`Dev origin ${JSON.stringify(origin)} is not a URL.`);
    }
    // Cognito only accepts plain-HTTP callbacks for localhost.
    if (url.protocol !== "https:") throw new Error(`Dev origin ${origin} must use https.`);
    if (url.origin !== origin) throw new Error(`Dev origin ${origin} must be a bare origin (no path or slash).`);
  }
  return [frontendUrl, ...devOrigins];
}

/** `devOrigins` context: a JSON array in cdk.json, or a comma-separated string from `-c`. */
export function parseDevOrigins(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") {
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}
