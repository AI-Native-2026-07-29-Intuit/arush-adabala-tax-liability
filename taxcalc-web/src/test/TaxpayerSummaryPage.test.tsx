// src/test/TaxpayerSummaryPage.test.tsx
import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import type { MockedResponse } from '@apollo/client/testing';
import { axe } from 'jest-axe';
import { renderWithProviders } from './renderWithProviders';
import { SummarizeTaxpayerDocument, type SummarizeTaxpayerMutation } from '../gql/generated/hooks';
import { TaxpayerSummaryPage } from '../pages/TaxpayerSummaryPage';

const VARIABLES = { id: 'stub-1' };

const HAPPY_DATA: SummarizeTaxpayerMutation = {
  summarizeTaxpayer: {
    __typename: 'TaxpayerSummary',
    filingStatus: 'SINGLE',
    totalLiability: 8420,
    jurisdictionCount: 2,
    riskBand: 'LOW',
  },
};

/**
 * A short, deliberate delay (mirroring the old MSW handler's `delay(200)`)
 * so tests observing the optimistic PENDING placeholder have a real window
 * before this resolves - without it, an in-memory resolver can settle
 * within the same microtask flush the placeholder renders in, and a test
 * never actually observes two distinct render states.
 */
function summarizeMock(delay = 200): MockedResponse {
  return { request: { query: SummarizeTaxpayerDocument, variables: VARIABLES }, result: { data: HAPPY_DATA }, delay };
}

function summarizeErrorMock(message: string): MockedResponse {
  return {
    request: { query: SummarizeTaxpayerDocument, variables: VARIABLES },
    // A plain GraphQLFormattedError object, not a `GraphQLError` class
    // instance - see TaxpayerListPage.test.tsx's errorMock for why.
    result: { errors: [{ message }] },
  };
}

function renderAtSummaryRoute(mocks: MockedResponse[]): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/taxpayers/:id/summary" element={<TaxpayerSummaryPage></TaxpayerSummaryPage>} />
    </Routes>,
    { mocks, initialEntries: ['/taxpayers/stub-1/summary'] },
  );
}

describe('TaxpayerSummaryPage', () => {
  it('renders a "Summarize" button and no summary before it is clicked', () => {
    renderAtSummaryRoute([summarizeMock()]);

    expect(screen.getByRole('button', { name: 'Summarize' })).toBeEnabled();
    expect(screen.queryByRole('definition')).not.toBeInTheDocument();
  });

  it('shows the optimistic placeholder immediately, then the server result', async () => {
    const { user } = renderAtSummaryRoute([summarizeMock()]);

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    // `loading` flips true synchronously inside useMutation's execute(),
    // before the network call is even made - so the placeholder is
    // already in the DOM the instant the click resolves, with no waitFor
    // needed. filingStatus and riskBand both carry the same placeholder
    // text. The mock still delays its response (see summarizeMock's own
    // comment) so the two waitFor calls below have a real window between
    // this synchronous placeholder and the real result landing.
    expect(screen.getAllByText('PENDING')).toHaveLength(2);

    await waitFor(() => expect(screen.getByText('SINGLE')).toBeInTheDocument());
    expect(screen.getByText('LOW')).toBeInTheDocument();
  });

  it('disables the button while the mutation is in flight', async () => {
    const { user } = renderAtSummaryRoute([summarizeMock()]);

    const button = screen.getByRole('button', { name: 'Summarize' });
    await user.click(button);

    expect(button).toBeDisabled();
    await waitFor(() => expect(button).toBeEnabled());
  });

  it('renders totalLiability and jurisdictionCount alongside filingStatus', async () => {
    const { user } = renderAtSummaryRoute([summarizeMock()]);

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    await waitFor(() => expect(screen.getByText('8420')).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders a role="alert" error banner when the mutation fails', async () => {
    const { user } = renderAtSummaryRoute([summarizeErrorMock('summary service down')]);

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/summary service down/i);
  });

  it('names the summary region for assistive tech', () => {
    renderAtSummaryRoute([summarizeMock()]);

    expect(screen.getByRole('region', { name: 'taxpayer-summary' })).toBeInTheDocument();
  });

  it('has no detectable accessibility violations once the summary has loaded', async () => {
    const { container, user } = renderAtSummaryRoute([summarizeMock()]);

    await user.click(screen.getByRole('button', { name: 'Summarize' }));
    await screen.findByText('SINGLE');

    // One scan on the loaded, happy-path state - not one per test above -
    // is the budget; the failure output names the rule and the offending
    // selector if this ever regresses.
    expect(await axe(container)).toHaveNoViolations();
  });
});
