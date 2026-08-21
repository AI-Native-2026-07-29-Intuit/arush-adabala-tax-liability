// src/test/useDebouncedSearch.test.tsx
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { useDebouncedSearch } from '../hooks/useDebouncedSearch';

beforeEach(() => {
  vi.useFakeTimers();
  useTaxpayerFilterStore.setState(useTaxpayerFilterStore.getInitialState(), true);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useDebouncedSearch', () => {
  it('lags the source by delayMs', () => {
    const { result } = renderHook(() => useDebouncedSearch(300));
    expect(result.current).toBe('');

    act(() => useTaxpayerFilterStore.getState().setSearchText('foo'));
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe(''); // still pre-debounce

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe('foo');
  });

  it('cancels the pending write when the source changes again', () => {
    const { result } = renderHook(() => useDebouncedSearch(300));

    act(() => useTaxpayerFilterStore.getState().setSearchText('foo'));
    act(() => {
      vi.advanceTimersByTime(100);
    });
    act(() => useTaxpayerFilterStore.getState().setSearchText('foobar'));
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe(''); // first timer cleared

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe('foobar');
  });
});
