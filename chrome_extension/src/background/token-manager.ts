import type { AuthState } from "../shared/types";
import { STORAGE_AUTH_KEY, ALARM_TOKEN_REFRESH, TOKEN_REFRESH_MINUTES, AUTH_BASE } from "../shared/constants";

/** Save auth state to chrome.storage.local */
export async function saveAuth(auth: AuthState): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_AUTH_KEY]: auth });
  // Start token refresh alarm
  chrome.alarms.create(ALARM_TOKEN_REFRESH, {
    delayInMinutes: TOKEN_REFRESH_MINUTES,
    periodInMinutes: TOKEN_REFRESH_MINUTES,
  });
}

/** Load auth state from chrome.storage.local */
export async function loadAuth(): Promise<AuthState | null> {
  const result = await chrome.storage.local.get(STORAGE_AUTH_KEY);
  return result[STORAGE_AUTH_KEY] ?? null;
}

/** Clear auth state */
export async function clearAuth(): Promise<void> {
  await chrome.storage.local.remove(STORAGE_AUTH_KEY);
  chrome.alarms.clear(ALARM_TOKEN_REFRESH);
}

/** Refresh the access token using refresh cookie */
export async function refreshToken(currentAuth: AuthState): Promise<AuthState | null> {
  try {
    const response = await fetch(`${AUTH_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${currentAuth.accessToken}`,
      },
    });

    if (!response.ok) return null;

    const data = await response.json();
    const updated: AuthState = {
      ...currentAuth,
      accessToken: data.access_token,
    };
    await saveAuth(updated);
    return updated;
  } catch {
    return null;
  }
}
