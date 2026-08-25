// src/test/useGetTaxLiabilityRest.test.tsx
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { useGetTaxLiabilityRest } from '../hooks/useGetTaxLiabilityRest';
import { server } from './server';

function wrapper({ children }: { readonly children: ReactNode }): React.ReactElement {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('useGetTaxLiabilityRest', () => {
  it('resolves data from the MSW-backed REST endpoint', async () => {
    const { result } = renderHook(() => useGetTaxLiabilityRest('stub-1'), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe('stub-1');
    expect(result.current.data?.filingStatus).toBe('SINGLE');
  });

  it('does not fetch while id is empty (enabled: Boolean(id))', () => {
    const { result } = renderHook(() => useGetTaxLiabilityRest(''), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('resolves null (not an error) on a 404', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers/:id', () => new HttpResponse(null, { status: 404 })),
    );

    const { result } = renderHook(() => useGetTaxLiabilityRest('missing-id'), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});
