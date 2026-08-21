// src/components/ThresholdSlider.tsx
type Props = {
  readonly value: number; // 0..100
  readonly onChange: (next: number) => void;
};

export function ThresholdSlider({ value, onChange }: Props): React.ReactElement {
  return (
    <label>
      Threshold
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.currentTarget.value))}
      />
    </label>
  );
}
