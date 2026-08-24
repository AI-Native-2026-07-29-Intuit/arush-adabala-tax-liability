// src/pages/TaxpayerSummaryPage.tsx
import { useMutation } from '@apollo/client';
import { useParams } from 'react-router-dom';
import { SummarizeTaxpayerDocument } from '../gql/generated/graphql';

const PENDING_PLACEHOLDER = 'PENDING';

/**
 * Triggers the `summarizeTaxpayer` mutation for the route's `:id` and
 * renders an instant placeholder card via `optimisticResponse` while the
 * real request is in flight - the placeholder carries `__typename:
 * 'TaxpayerSummary'` so Apollo's cache can normalize it the same way as
 * the eventual server result, and swaps the placeholder out automatically
 * once that result lands.
 */
export function TaxpayerSummaryPage(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();
  const [summarize, { loading, data, error }] = useMutation(SummarizeTaxpayerDocument, {
    variables: { id },
    optimisticResponse: {
      summarizeTaxpayer: {
        __typename: 'TaxpayerSummary',
        filingStatus: PENDING_PLACEHOLDER,
        totalLiability: 0,
        jurisdictionCount: 0,
        riskBand: PENDING_PLACEHOLDER,
      },
    },
  });

  return (
    <section aria-label="taxpayer-summary">
      <button type="button" onClick={() => summarize()} disabled={loading}>
        Summarize
      </button>
      {error && <p role="alert">Error: {error.message}</p>}
      {data && (
        <dl>
          <dt>filingStatus</dt> <dd>{data.summarizeTaxpayer.filingStatus}</dd>
          <dt>totalLiability</dt> <dd>{data.summarizeTaxpayer.totalLiability}</dd>
          <dt>jurisdictionCount</dt> <dd>{data.summarizeTaxpayer.jurisdictionCount}</dd>
          <dt>riskBand</dt> <dd>{data.summarizeTaxpayer.riskBand}</dd>
        </dl>
      )}
    </section>
  );
}
