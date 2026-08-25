// src/stores/useTaxpayerFilterStore.ts
import { create } from 'zustand';
import { createJSONStorage, devtools, persist } from 'zustand/middleware';
import { safeLocalStorage } from '../lib/safeLocalStorage';

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
        // Only `threshold` persists — a saved `searchText` would silently refilter results on a later, unrelated visit.
        partialize: (s) => ({ threshold: s.threshold }),
      },
    ),
    { name: 'useTaxpayerFilterStore' },
  ),
);
