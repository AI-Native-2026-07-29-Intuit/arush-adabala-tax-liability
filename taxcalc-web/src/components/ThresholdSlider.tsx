// src/components/ThresholdSlider.tsx
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';

/**
 * Controlled by `useTaxpayerFilterStore`'s `threshold` slice (W4 D2) rather
 * than a `value`/`onChange` prop pair, so a sibling elsewhere in the tree
 * can read or drive the same value without prop drilling.
 */
export function ThresholdSlider(): React.ReactElement {
  const threshold = useTaxpayerFilterStore((s) => s.threshold);
  const setThreshold = useTaxpayerFilterStore((s) => s.setThreshold);

  return (
    <label>
      Threshold
      <input
        type="range"
        min={0}
        max={100}
        value={threshold}
        onChange={(e) => setThreshold(Number(e.currentTarget.value))}
      />
    </label>
  );
}
