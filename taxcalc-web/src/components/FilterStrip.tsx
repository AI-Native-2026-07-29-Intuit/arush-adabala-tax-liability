// src/components/FilterStrip.tsx
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';

// Display labels are deliberately distinct strings from the raw status
// codes (not just a different case): the detail card below renders the
// selected taxpayer's own `filingStatus` code (e.g. "SINGLE") as plain
// text, and an exact-text query for that code must resolve to one element,
// not one here plus one there.
const FILING_STATUSES: ReadonlyArray<{ readonly code: string; readonly label: string }> = [
  { code: 'SINGLE', label: 'Single' },
  { code: 'MFJ', label: 'Married filing jointly' },
  { code: 'MFS', label: 'Married filing separately' },
  { code: 'HOH', label: 'Head of household' },
  { code: 'QW', label: 'Qualifying widow(er)' },
];

/**
 * Filters strip rendered above the detail card. Each child subscribes to
 * its own `useTaxpayerFilterStore` slice, rather than the strip subscribing
 * once to the whole store, so a change to one field only re-renders the
 * control that owns it.
 */
export function FilterStrip(): React.ReactElement {
  return (
    <section aria-label="Filters">
      <FilingStatusChips />
      <DateRangeInputs />
      <SearchTextInput />
      <IncludeArchivedToggle />
    </section>
  );
}

/** Checkbox group over the `filingStatusFilter` slice; toggling one code updates the array. */
function FilingStatusChips(): React.ReactElement {
  const filingStatusFilter = useTaxpayerFilterStore((s) => s.filingStatusFilter);
  const setFilingStatusFilter = useTaxpayerFilterStore((s) => s.setFilingStatusFilter);

  const toggle = (status: string): void => {
    const next = filingStatusFilter.includes(status)
      ? filingStatusFilter.filter((current) => current !== status)
      : [...filingStatusFilter, status];
    setFilingStatusFilter(next);
  };

  return (
    <fieldset>
      <legend>Filing status</legend>
      {FILING_STATUSES.map(({ code, label }) => (
        <label key={code}>
          <input
            type="checkbox"
            checked={filingStatusFilter.includes(code)}
            onChange={() => toggle(code)}
          />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

/** Read-only view of the `dateRange` slice; no setter ships in the Task 1 store. */
function DateRangeInputs(): React.ReactElement {
  const dateRange = useTaxpayerFilterStore((s) => s.dateRange);

  return (
    <label>
      Date range
      {/* No setter ships for `dateRange` in the Task 1 store (four setters
          + reset, per the deliverable spec) — read-only until a later day
          wires one up. */}
      <input type="date" value={dateRange[0]} readOnly />
      <input type="date" value={dateRange[1] ?? ''} readOnly />
    </label>
  );
}

/** Text input bound to the `searchText` slice (consumed debounced via {@link useDebouncedSearch}). */
function SearchTextInput(): React.ReactElement {
  const searchText = useTaxpayerFilterStore((s) => s.searchText);
  const setSearchText = useTaxpayerFilterStore((s) => s.setSearchText);

  return (
    <label>
      Search
      <input
        type="text"
        value={searchText}
        onChange={(e) => setSearchText(e.currentTarget.value)}
      />
    </label>
  );
}

/** Checkbox bound to the `includeArchived` slice. */
function IncludeArchivedToggle(): React.ReactElement {
  const includeArchived = useTaxpayerFilterStore((s) => s.includeArchived);
  const setIncludeArchived = useTaxpayerFilterStore((s) => s.setIncludeArchived);

  return (
    <label>
      <input
        type="checkbox"
        checked={includeArchived}
        onChange={(e) => setIncludeArchived(e.currentTarget.checked)}
      />
      Include archived
    </label>
  );
}
