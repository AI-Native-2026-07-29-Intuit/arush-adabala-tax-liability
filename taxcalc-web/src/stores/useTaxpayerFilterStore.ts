// src/stores/useTaxpayerFilterStore.ts
import { create } from 'zustand';
import { createJSONStorage, devtools, persist } from 'zustand/middleware';
import type { StateStorage } from 'zustand/middleware';

type FilterState = {
  readonly filingStatusFilter: ReadonlyArray<string>;
  readonly dateRange: readonly [string, string | null];
  readonly searchText: string;
  readonly includeArchived: boolean;
  readonly threshold: number;
};

type FilterActions = {
  readonly setFilingStatusFilter: (next: ReadonlyArray<string>) => void;
  readonly setSearchText: (next: string) => void;
  readonly setThreshold: (next: number) => void;
  readonly setIncludeArchived: (next: boolean) => void;
  readonly reset: () => void;
};

const memoryFallback = new Map<string, string>();

/**
 * Falls back to an in-memory Map when `window.localStorage` is missing or
 * throws — Safari private browsing raises `SecurityError` on `setItem`,
 * and (hit while building this store) Node 20+'s experimental global
 * `localStorage` can shadow jsdom's working implementation under Vitest,
 * leaving `window.localStorage` `undefined`. Either way the filter state
 * should degrade to session-only rather than crash the app.
 */
const safeLocalStorage: StateStorage = {
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

const INITIAL: FilterState = {
  filingStatusFilter: [],
  dateRange: ['', null],
  searchText: '',
  includeArchived: false,
  threshold: 50,
};

/**
 * Cross-cutting filter/threshold state for the taxpayer detail page and its
 * filter strip, hoisted out of per-component `useState` so a sibling three
 * levels up the tree (or a future route) can read the same slices.
 */
export const useTaxpayerFilterStore = create<FilterState & FilterActions>()(
  devtools(
    persist(
      (set) => ({
        ...INITIAL,
        setFilingStatusFilter: (next) =>
          set({ filingStatusFilter: next }, false, 'filters/setFilingStatusFilter'),
        setSearchText: (next) => set({ searchText: next }, false, 'filters/setSearchText'),
        setThreshold: (next) => set({ threshold: next }, false, 'filters/setThreshold'),
        setIncludeArchived: (next) =>
          set({ includeArchived: next }, false, 'filters/setIncludeArchived'),
        reset: () => set(INITIAL, false, 'filters/reset'),
      }),
      {
        name: 'taxcalc-web:filters',
        storage: createJSONStorage(() => safeLocalStorage),
        // Only `threshold` survives a reload. Search text persisted across
        // reloads would be a UX bug: an engineer types "foo" for an
        // unrelated reason, returns next week, and sees results still
        // filtered by "foo" — the filter chips and date range are session
        // state, not saved preferences.
        partialize: (s) => ({ threshold: s.threshold }),
      },
    ),
    { name: 'useTaxpayerFilterStore' },
  ),
);
