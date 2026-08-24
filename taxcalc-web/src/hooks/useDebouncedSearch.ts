// src/hooks/useDebouncedSearch.ts
import { useEffect, useState } from 'react';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';

/**
 * Reads `searchText` from the Zustand filter store and returns a value that
 * lags the source by `delayMs` milliseconds. Cleanup clears the pending
 * timer on every keystroke and on unmount; without that cleanup the timer
 * fires after the component is gone.
 */
export function useDebouncedSearch(delayMs = 300): string {
  const searchText = useTaxpayerFilterStore((s) => s.searchText);
  const [debounced, setDebounced] = useState<string>(searchText);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(searchText), delayMs);
    return () => clearTimeout(timer);
  }, [searchText, delayMs]);

  return debounced;
}
