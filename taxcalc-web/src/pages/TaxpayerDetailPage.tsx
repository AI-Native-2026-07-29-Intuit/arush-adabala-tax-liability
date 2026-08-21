// src/pages/TaxpayerDetailPage.tsx
import { useEffect, useReducer, useState } from 'react';
import type { Taxpayer } from '../types/taxpayer';
import { detailReducer, INITIAL_DETAIL_STATE, type DetailState } from './TaxpayerDetailPage.reducer';
import { useDebouncedSearch } from '../hooks/useDebouncedSearch';
import { FilterStrip } from '../components/FilterStrip';
import { ThresholdSlider } from '../components/ThresholdSlider';
import { ThresholdReadout } from '../components/ThresholdReadout';

const MOCK_TAXPAYER_URL = '/mocks/taxpayer.json';

export function TaxpayerDetailPage(): React.ReactElement {
  // The W4 D1 useTaxpayer(id) hook's data|loading|error shape is replaced
  // by a useReducer state machine (idle|loading|success|error|empty) so
  // the render tree branches on state.status and TypeScript narrows each
  // branch automatically. The fetch itself moves into this page's own
  // effect, dispatching fetch/start up front and fetch/success|fetch/error
  // on resolution - the same shape Apollo's data|error result will line up
  // with on W4 D3.
  const [state, dispatch] = useReducer(detailReducer, INITIAL_DETAIL_STATE);

  // `threshold` (and the other filter fields) now live in
  // useTaxpayerFilterStore (W4 D2) instead of a page-owned useState;
  // ThresholdSlider and ThresholdReadout each read/write their own slice
  // directly, so no value/onChange props are threaded through here.
  const debouncedSearchText = useDebouncedSearch();

  // React error boundaries only catch errors thrown during rendering, not
  // from event handlers - so the dev-only trigger button sets state and
  // lets the resulting re-render do the throwing, rather than throwing
  // directly in its onClick.
  const [shouldThrow, setShouldThrow] = useState(false);
  if (shouldThrow) {
    throw new Error('Manually triggered error (dev-only "Trigger error" button)');
  }

  useEffect(() => {
    let cancelled = false;
    dispatch({ type: 'fetch/start' });
    fetch(MOCK_TAXPAYER_URL)
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json() as Promise<Taxpayer | null>;
      })
      .then((payload) => {
        if (!cancelled) dispatch({ type: 'fetch/success', payload });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        dispatch({ type: 'fetch/error', error: message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main aria-labelledby="taxpayer-heading">
      <FilterStrip></FilterStrip>
      <p>filtering for: &apos;{debouncedSearchText}&apos;</p>

      <DetailCard state={state}></DetailCard>

      {import.meta.env.DEV && (
        <button type="button" onClick={() => setShouldThrow(true)}>
          Trigger error
        </button>
      )}
    </main>
  );
}

function DetailCard({ state }: { readonly state: DetailState }): React.ReactElement {
  switch (state.status) {
    case 'idle':
    case 'loading':
      return <p>Loading…</p>;
    case 'error':
      return <p role="alert">Failed to load: {state.error}</p>;
    case 'empty':
      return <p>Not found.</p>;
    case 'success': {
      const { data } = state;
      return (
        <>
          <h1 id="taxpayer-heading">Taxpayer {data.id}</h1>
          <dl>
            <dt>filingStatus</dt>              <dd>{data.filingStatus}</dd>
            <dt>jurisdictionCount</dt>         <dd>{data.jurisdictionCount}</dd>
            <dt>totalLiability</dt>            <dd>{data.totalLiability}</dd>
          </dl>

          <section aria-label="Threshold control">
            <ThresholdSlider></ThresholdSlider>
            <ThresholdReadout></ThresholdReadout>
          </section>
        </>
      );
    }
  }
}
