/**
 * Recent friends for the "Who's here?" chips. Stored only in this browser (localStorage), per
 * signed-in account: friends' names never leave the device. Clearing site data resets them.
 */

export interface Friend {
  name: string;
  timesUsed: number;
  lastUsedAt: string;
}

export const MAX_FRIENDS = 50;
const key = (userId: string) => `onetally.friends.${userId}`;

// Storage can be missing or throw (private mode, blocked site data): chips are a convenience.
function read(userId: string): Friend[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key(userId)) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((f) => typeof f?.name === "string") : [];
  } catch {
    return [];
  }
}

function write(userId: string, friends: Friend[]): void {
  try {
    localStorage.setItem(key(userId), JSON.stringify(friends));
  } catch {
    // ignore: nothing else depends on it
  }
}

const ranked = (friends: Friend[]) =>
  [...friends].sort((a, b) => b.timesUsed - a.timesUsed || b.lastUsedAt.localeCompare(a.lastUsedAt));

/** Most-used first, then most recent. */
export function loadFriends(userId: string): Friend[] {
  return ranked(read(userId)).slice(0, MAX_FRIENDS);
}

/** Count a split with these people; the latest spelling wins; keeps the top MAX_FRIENDS. */
export function rememberFriends(userId: string, names: string[], now: Date = new Date()): void {
  const friends = read(userId);
  for (const name of names) {
    const existing = friends.find((f) => f.name.toLocaleLowerCase() === name.toLocaleLowerCase());
    if (existing) {
      existing.name = name;
      existing.timesUsed += 1;
      existing.lastUsedAt = now.toISOString();
    } else {
      friends.push({ name, timesUsed: 1, lastUsedAt: now.toISOString() });
    }
  }
  write(userId, ranked(friends).slice(0, MAX_FRIENDS));
}

export function forgetFriend(userId: string, name: string): void {
  write(
    userId,
    read(userId).filter((f) => f.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
  );
}
