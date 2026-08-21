// src/pages/TaxpayerDetailPage.tsx
import { useState } from 'react';
import { useTaxpayer } from '../hooks/useTaxpayer';
import { ThresholdSlider } from '../components/ThresholdSlider';
import { ThresholdReadout } from '../components/ThresholdReadout';

export function TaxpayerDetailPage(): React.ReactElement {
  // (1) `threshold` is owned HERE - the page is the source of truth.
  //     ThresholdSlider mutates it via the onChange prop; ThresholdReadout
  //     reads it via the value prop. Two siblings, one source.
  const [threshold, setThreshold] = useState<number>(50);

  const { data, loading, error } = useTaxpayer('stub-id-1');

  if (loading) return <p>Loading…</p>;
  if (error) return <p role="alert">Failed to load: {error}</p>;
  if (data === null) return <p>Not found.</p>;

  return (
    <main aria-labelledby="taxpayer-heading">
      <h1 id="taxpayer-heading">Taxpayer {data.id}</h1>
      <dl>
        <dt>filingStatus</dt>              <dd>{data.filingStatus}</dd>
        <dt>jurisdictionCount</dt>         <dd>{data.jurisdictionCount}</dd>
        <dt>totalLiability</dt>            <dd>{data.totalLiability}</dd>
      </dl>

      <section aria-label="Threshold control">
        <ThresholdSlider value={threshold} onChange={setThreshold}></ThresholdSlider>
        <ThresholdReadout value={threshold}></ThresholdReadout>
      </section>
    </main>
  );
}
