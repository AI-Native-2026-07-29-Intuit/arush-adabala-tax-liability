// src/hooks/useGetTaxLiabilityRest.ts
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { getStoredJwt } from '../lib/jwtStorage';

/** Mirrors `TaxpayerReadModel.EmbeddedLiability`'s JSON shape. */
export type EmbeddedLiabilityRest = {
  readonly taxYear: number;
  readonly bracketId: string;
  // Backend BigDecimal fields serialize as JSON numbers (no
  // @JsonFormat(shape = STRING) is configured), so these are `number`
  // here rather than the string-per-BigDecimal convention the rest of
  // this codebase otherwise follows - see types/taxpayer.ts for the
  // string-typed version used by the still-mocked TaxpayerDetailPage data.
  readonly taxableAmount: number;
  readonly liabilityAmount: number;
  readonly computedAt: string; // Instant, ISO-8601
};

/** Mirrors `TaxpayerReadModel`'s JSON shape returned by `GET /api/v1/taxpayers/{id}`. */
export type TaxpayerRest = {
  readonly id: string;
  readonly displayName: string;
  readonly filingStatus: string;
  readonly homeJurisdiction: string;
  readonly createdAt: string; // Instant, ISO-8601
  readonly liabilities: ReadonlyArray<EmbeddedLiabilityRest>;
  readonly tags: ReadonlyArray<string>;
};

const API_BASE_URL = 'http://localhost:8080/api/v1/taxpayers';

/**
 * REST counterpart to the GraphQL hooks in `pages/TaxpayerListPage.tsx` /
 * `pages/TaxpayerSummaryPage.tsx`: fetches a single taxpayer's read-model
 * document. A 404 resolves to `null` data rather than throwing, so
 * `TaxpayerDetailPage`'s D2 reducer can keep treating "not found" as its
 * own `empty` state instead of folding it into `error`.
 */
export function useGetTaxLiabilityRest(id: string): UseQueryResult<TaxpayerRest | null, Error> {
  return useQuery({
    queryKey: ['taxcalc', id],
    enabled: Boolean(id),
    queryFn: async (): Promise<TaxpayerRest | null> => {
      const token = getStoredJwt();
      const res = await fetch(`${API_BASE_URL}/${id}`, {
        headers: token ? { authorization: `Bearer ${token}` } : {},
      });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as TaxpayerRest;
    },
  });
}
