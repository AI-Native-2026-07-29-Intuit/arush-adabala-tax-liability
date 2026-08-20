// src/test/TaxpayerDetailPage.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { TaxpayerDetailPage } from '../pages/TaxpayerDetailPage';

const MOCK = {
  id: 'stub-id-1',
  filingStatus: 'SINGLE',
  jurisdictionCount: 3,
  totalLiability: '8420.00',
  lines: [
    { id: 'line-1', amount: '100.00' },
    { id: 'line-2', amount: '250.00' },
  ],
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(MOCK)))));
});

describe('TaxpayerDetailPage', () => {
  it('renders entity id and a sample field from the mock JSON', async () => {
    render(<TaxpayerDetailPage></TaxpayerDetailPage>);
    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-id-1'));
    expect(screen.getByText('SINGLE')).toBeInTheDocument();
  });

  it('updates the readout when the slider is moved (lifted state)', async () => {
    render(<TaxpayerDetailPage></TaxpayerDetailPage>);
    await waitFor(() => screen.getByRole('heading'));

    const slider = screen.getByLabelText('Threshold');
    fireEvent.change(slider, { target: { value: '51' } });

    expect(screen.getByRole('status')).toHaveTextContent(/Threshold:\s*51%/);
  });
});
