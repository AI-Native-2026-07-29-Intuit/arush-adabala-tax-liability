// src/pages/TaxpayerListPage.tsx
import { Link } from 'react-router-dom';
import { useLatestTaxpayersQuery } from '../gql/generated/hooks';

/** Lists the most recently updated taxpayers via the `latestTaxpayers` GraphQL query. */
export function TaxpayerListPage(): React.ReactElement {
  const { loading, error, data } = useLatestTaxpayersQuery({
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
