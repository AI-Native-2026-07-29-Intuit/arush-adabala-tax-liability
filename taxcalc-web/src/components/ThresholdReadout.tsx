// src/components/ThresholdReadout.tsx
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';

/** Read-only view of `useTaxpayerFilterStore`'s `threshold` slice (W4 D2). */
export function ThresholdReadout(): React.ReactElement {
  const threshold = useTaxpayerFilterStore((s) => s.threshold);
  return <output aria-live="polite">Threshold: {threshold}%</output>;
}
