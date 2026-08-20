// src/components/ThresholdReadout.tsx
type Props = { readonly value: number };

export function ThresholdReadout({ value }: Props): React.ReactElement {
  return <output aria-live="polite">Threshold: {value}%</output>;
}
