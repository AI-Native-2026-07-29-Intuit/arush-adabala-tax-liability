// src/pages/TaxpayerListPage.tsx
import type { ApolloError } from '@apollo/client';
import { Link } from 'react-router-dom';
import { useLatestTaxpayersQuery, type LatestTaxpayersQuery } from '../gql/generated/hooks';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { useDebouncedSearch } from '../hooks/useDebouncedSearch';

/**
 * Lists the most recently updated taxpayers via the `latestTaxpayers`
 * GraphQL query. The search box narrows the already-fetched rows
 * client-side by id - it reuses `useTaxpayerFilterStore`'s `searchText`
 * slice (the same store `TaxpayerDetailPage`'s `FilterStrip` reads), per
 * that store's own doc comment anticipating "a future route" sharing it,
 * rather than a page-local `useState` duplicating the same concept.
 */
export function TaxpayerListPage(): React.ReactElement {
  const { loading, error, data } = useLatestTaxpayersQuery({
    variables: { limit: 20 },
  });
  const searchText = useTaxpayerFilterStore((s) => s.searchText);
  const setSearchText = useTaxpayerFilterStore((s) => s.setSearchText);
  const debouncedSearchText = useDebouncedSearch();

  return (
    <>
      <h1>Taxpayers</h1>
      <label>
        Search
        <input
          type="text"
          value={searchText}
          onChange={(e) => setSearchText(e.currentTarget.value)}
        />
      </label>
      <TaxpayerListBody loading={loading} error={error} data={data} debouncedSearchText={debouncedSearchText} />
    </>
  );
}

interface TaxpayerListBodyProps {
  readonly loading: boolean;
  readonly error: ApolloError | undefined;
  readonly data: LatestTaxpayersQuery | undefined;
  readonly debouncedSearchText: string;
}

/** Renders the loading/error/empty/list branch, separately from the always-visible heading and search box above it. */
function TaxpayerListBody({ loading, error, data, debouncedSearchText }: TaxpayerListBodyProps): React.ReactElement {
  if (loading) return <p role="status">Loading…</p>;
  if (error) return <p role="alert">Error: {error.message}</p>;

  const allRows = data?.latestTaxpayers ?? [];
  const needle = debouncedSearchText.trim().toLowerCase();
  const rows = needle === '' ? allRows : allRows.filter((r) => r.id.toLowerCase().includes(needle));

  if (rows.length === 0) return <p role="status">No taxpayers yet.</p>;

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
