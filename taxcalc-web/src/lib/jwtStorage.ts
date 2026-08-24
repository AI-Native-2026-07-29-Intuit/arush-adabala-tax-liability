// src/lib/jwtStorage.ts
//
// Shared accessors for the app's single auth token, read by the Apollo
// auth link, the REST hook, and the router's ProtectedLayout, and written
// by LoginPage. Wrapped in try/catch rather than calling
// `window.localStorage` directly - Safari private browsing raises
// `SecurityError` on `setItem`, and (the same issue useTaxpayerFilterStore.ts
// hit) Node 20+'s own experimental `localStorage` global can shadow
// jsdom's working implementation under Vitest, leaving `window.localStorage`
// `undefined`. Either way, falling back to "no token" is safe: every
// consumer already has a not-authenticated code path.
export const JWT_STORAGE_KEY = 'uc:jwt';

export function getStoredJwt(): string | null {
  try {
    return window.localStorage.getItem(JWT_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredJwt(token: string): void {
  try {
    window.localStorage.setItem(JWT_STORAGE_KEY, token);
  } catch {
    // best-effort; see the module comment above.
  }
}
