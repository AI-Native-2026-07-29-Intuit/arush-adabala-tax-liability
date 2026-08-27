// src/pages/TaxpayerDetailPage.tsx
import { useEffect, useReducer, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useGetTaxLiabilityRest } from '../hooks/useGetTaxLiabilityRest';
import { detailReducer, INITIAL_DETAIL_STATE, type DetailState } from './TaxpayerDetailPage.reducer';
import { useDebouncedSearch } from '../hooks/useDebouncedSearch';
import { FilterStrip } from '../components/FilterStrip';
import { ThresholdSlider } from '../components/ThresholdSlider';
import { ThresholdReadout } from '../components/ThresholdReadout';

/**
 * Detail page for a single taxpayer, routed at `/taxpayers/:id`. Fetch
 * progress is a useReducer state machine (see {@link detailReducer});
 * cross-cutting filter/threshold fields live in `useTaxpayerFilterStore`
 * rather than local state. Wrapped in an `ErrorBoundary` by `App.tsx`.
 */
export function TaxpayerDetailPage(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();

  // The W4 D1 useTaxpayer(id) hook's data|loading|error shape, then W4 D2's
  // own fetch effect, are both replaced by useGetTaxLiabilityRest - a
  // TanStack Query hook against the live REST endpoint. This page still
  // drives the same idle|loading|success|error|empty reducer from D2
  // rather than branching on the query result directly, so the render
  // tree keeps narrowing on state.status the same way it always has.
  const [state, dispatch] = useReducer(detailReducer, INITIAL_DETAIL_STATE);
  const { data, isLoading, isError, error, isSuccess } = useGetTaxLiabilityRest(id);

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
    if (isLoading) {
      dispatch({ type: 'fetch/start' });
    } else if (isError) {
      dispatch({ type: 'fetch/error', error: error.message });
    } else if (isSuccess) {
      dispatch({ type: 'fetch/success', payload: data });
    }
  }, [isLoading, isError, error, isSuccess, data]);

  return (
    <main aria-labelledby="taxpayer-heading">
      <FilterStrip></FilterStrip>
      <p>filtering for: &apos;{debouncedSearchText}&apos;</p>

      <DetailCard state={state} debouncedSearchText={debouncedSearchText}></DetailCard>

      {import.meta.env.DEV && (
        <button type="button" onClick={() => setShouldThrow(true)}>
          Trigger error
        </button>
      )}
    </main>
  );
}

interface DetailCardProps {
  readonly state: DetailState;
  /** Narrows the liabilities table to rows whose bracket matches this substring. */
  readonly debouncedSearchText: string;
}

/** Renders the branch of {@link DetailState} the fetch has reached. */
function DetailCard({ state, debouncedSearchText }: DetailCardProps): React.ReactElement {
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
      const needle = debouncedSearchText.trim().toLowerCase();
      const liabilities =
        needle === ''
          ? data.liabilities
          : data.liabilities.filter((l) => l.bracketId.toLowerCase().includes(needle));

      return (
        <>
          <h1 id="taxpayer-heading">Taxpayer {data.displayName} ({data.id})</h1>
          <dl>
            <dt>filingStatus</dt>       <dd>{data.filingStatus}</dd>
            <dt>homeJurisdiction</dt>   <dd>{data.homeJurisdiction}</dd>
            <dt>tags</dt>               <dd>{data.tags.length > 0 ? data.tags.join(', ') : '—'}</dd>
          </dl>

          {/* Tabular financial data (year/bracket/amount) - a <table> gives
              each cell a real role="cell" / row="row", which a bare <ul>
              never did, and reads better with a screen reader's table
              navigation than a flat list of concatenated strings would. */}
          <table>
            <caption>Liabilities</caption>
            <thead>
              <tr>
                <th scope="col">Tax year</th>
                <th scope="col">Bracket</th>
                <th scope="col">Amount</th>
              </tr>
            </thead>
            <tbody>
              {liabilities.map((l) => (
                <tr key={`${l.taxYear}-${l.bracketId}`}>
                  <td>{l.taxYear}</td>
                  <td>{l.bracketId}</td>
                  <td>{l.liabilityAmount}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <section aria-label="Threshold control">
            <ThresholdSlider></ThresholdSlider>
            <ThresholdReadout></ThresholdReadout>
          </section>

          <Link to={`/taxpayers/${data.id}/chat`}>Chat about {data.id}</Link>
        </>
      );
    }
  }
}
