// src/lib/safeLocalStorage.ts
import type { StateStorage } from 'zustand/middleware';

const memoryFallback = new Map<string, string>();

/**
 * Falls back to an in-memory Map when `window.localStorage` is missing or
 * throws — Safari private browsing raises `SecurityError` on `setItem`,
 * and (hit while building `useTaxpayerFilterStore`) Node 20+'s
 * experimental global `localStorage` can shadow jsdom's working
 * implementation under Vitest, leaving `window.localStorage` `undefined`
 * outright. Either way, a Zustand `persist` store should degrade to
 * session-only rather than crash the app or silently drop writes -
 * shared here after `useTaxpayerChatStore` (W4 D4) hit the exact same
 * `window.localStorage === undefined` failure `useTaxpayerFilterStore`
 * (W4 D2) had already worked around.
 */
export const safeLocalStorage: StateStorage = {
  getItem: (name) => {
    try {
      return window.localStorage.getItem(name);
    } catch {
      return memoryFallback.get(name) ?? null;
    }
  },
  setItem: (name, value) => {
    try {
      window.localStorage.setItem(name, value);
    } catch {
      memoryFallback.set(name, value);
    }
  },
  removeItem: (name) => {
    try {
      window.localStorage.removeItem(name);
    } catch {
      memoryFallback.delete(name);
    }
  },
};
