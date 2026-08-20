// src/hooks/useTaxpayer.ts
import { useEffect, useState } from 'react';
import type { Taxpayer } from '../types/taxpayer';

type State =
  | { status: 'loading' }
  | { status: 'ok'; data: Taxpayer }
  | { status: 'error'; error: string };

/**
 * Stub data source for W4 D1. The real Apollo Client query lands on W4 D3.
 *
 * @param id - the taxpayer id; today only "stub-id-1" returns data.
 */
export function useTaxpayer(id: string): {
  data: Taxpayer | null;
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<State>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetch(`/mocks/taxpayer.json`)
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json() as Promise<Taxpayer>;
      })
      .then((data) => { if (!cancelled) setState({ status: 'ok', data }); })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setState({ status: 'error', error: message });
      });
    return () => { cancelled = true; };
  }, [id]);

  return {
    data: state.status === 'ok' ? state.data : null,
    loading: state.status === 'loading',
    error: state.status === 'error' ? state.error : null,
  };
}
