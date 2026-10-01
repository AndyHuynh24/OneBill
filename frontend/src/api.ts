/** The PROFILE item as returned by GET /me (services/api/me/logic.py). */
export interface Profile {
  userId: string;
  baseCurrency: string;
  createdAt: string;
}

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Returns the current Cognito access token, or undefined when signed out. */
export type TokenGetter = () => Promise<string | undefined>;

export async function fetchProfile(apiUrl: string, getToken: TokenGetter): Promise<Profile> {
  const token = await getToken();
  if (!token) {
    throw new ApiError("Not signed in", 401);
  }
  const response = await fetch(new URL("me", apiUrl).toString(), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiError("Request failed", response.status);
  }
  return (await response.json()) as Profile;
}
