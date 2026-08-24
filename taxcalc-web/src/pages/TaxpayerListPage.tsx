// src/pages/TaxpayerListPage.tsx
import { useQuery } from '@apollo/client';
import { Link } from 'react-router-dom';
import { LatestTaxpayersDocument } from '../gql/generated/graphql';

/**
 * Lists the most recently updated taxpayers via the `latestTaxpayers`
 * GraphQL query. `LatestTaxpayersDocument` is a `TypedDocumentNode`, so
 * `useQuery` infers its `data`/variables shape without a separate
 * generated hook - the same pattern `TaxpayerSummaryPage`'s mutation uses.
 */
export function TaxpayerListPage(): React.ReactElement {
  const { loading, error, data } = useQuery(LatestTaxpayersDocument, {
    variables: { limit: 20 },
  });

  if (loading) return <p role="status">Loading…</p>;
  if (error) return <p role="alert">Error: {error.message}</p>;

  const rows = data?.latestTaxpayers ?? [];
  if (rows.length === 0) return <p>No taxpayers yet.</p>;

  return (
    <ul aria-label="taxpayer-list">
      {rows.map((r) => (
        <li key={r.id}>
          <Link to={`/taxpayers/${r.id}`}>{r.id}</Link>
          {r.tags.length > 0 && <span> ({r.tags.join(', ')})</span>}
        </li>
      ))}
    </ul>
  );
}
