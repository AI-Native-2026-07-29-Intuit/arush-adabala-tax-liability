// src/pages/TaxpayerDetailPage.tsx
import { useTaxpayer } from '../hooks/useTaxpayer';
import { FilterStrip } from '../components/FilterStrip';
import { ThresholdSlider } from '../components/ThresholdSlider';
import { ThresholdReadout } from '../components/ThresholdReadout';

export function TaxpayerDetailPage(): React.ReactElement {
  // `threshold` (and the other filter fields) now live in
  // useTaxpayerFilterStore (W4 D2) instead of a page-owned useState;
  // ThresholdSlider and ThresholdReadout each read/write their own slice
  // directly, so no value/onChange props are threaded through here.
  const { data, loading, error } = useTaxpayer('stub-id-1');

  if (loading) return <p>Loading…</p>;
  if (error) return <p role="alert">Failed to load: {error}</p>;
  if (data === null) return <p>Not found.</p>;

  return (
    <main aria-labelledby="taxpayer-heading">
      <FilterStrip></FilterStrip>

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
    </main>
  );
}
