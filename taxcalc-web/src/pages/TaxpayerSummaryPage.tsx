// src/pages/TaxpayerSummaryPage.tsx
import { useParams } from 'react-router-dom';
import { useSummarizeTaxpayerMutation } from '../gql/generated/hooks';

const PENDING_PLACEHOLDER = 'PENDING';

const PLACEHOLDER_SUMMARY = {
  filingStatus: PENDING_PLACEHOLDER,
  totalLiability: 0,
  jurisdictionCount: 0,
  riskBand: PENDING_PLACEHOLDER,
};

/**
 * Triggers the `summarizeTaxpayer` mutation for the route's `:id` and
 * renders an instant placeholder card while the real request is in
 * flight. `useMutation`'s own `data` only ever reflects the *server*
 * result - `optimisticResponse` writes straight into Apollo's cache for
 * any `useQuery` observing the same fields, which `TaxpayerSummary` has
 * none of (it's reachable only via this mutation, not any query), so the
 * placeholder shown here is driven off `loading` instead. The mutation
 * still carries `optimisticResponse` (with `__typename: 'TaxpayerSummary'`
 * so Apollo's cache can normalize the write) because that's what a
 * consuming query elsewhere in the app would need to see the same instant
 * update - this page just isn't that consumer.
 */
export function TaxpayerSummaryPage(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();
  const [summarize, { loading, data, error }] = useSummarizeTaxpayerMutation({
    variables: { id },
    optimisticResponse: {
      summarizeTaxpayer: { __typename: 'TaxpayerSummary', ...PLACEHOLDER_SUMMARY },
    },
  });

  const summary = data?.summarizeTaxpayer ?? (loading ? PLACEHOLDER_SUMMARY : null);

  return (
    <section aria-label="taxpayer-summary">
      <button type="button" onClick={() => summarize()} disabled={loading}>
        Summarize
      </button>
      {error && <p role="alert">Error: {error.message}</p>}
      {summary && (
        <dl>
          <dt>filingStatus</dt> <dd>{summary.filingStatus}</dd>
          <dt>totalLiability</dt> <dd>{summary.totalLiability}</dd>
          <dt>jurisdictionCount</dt> <dd>{summary.jurisdictionCount}</dd>
          <dt>riskBand</dt> <dd>{summary.riskBand}</dd>
        </dl>
      )}
    </section>
  );
}
