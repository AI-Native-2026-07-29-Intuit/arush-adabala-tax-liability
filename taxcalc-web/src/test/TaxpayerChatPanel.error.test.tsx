// src/test/TaxpayerChatPanel.error.test.tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { TaxpayerChatPanel } from '../pages/TaxpayerChatPanel';
import { useTaxpayerChatStore } from '../stores/useTaxpayerChatStore';
import { server } from './server';

beforeEach(() => {
  useTaxpayerChatStore.setState(useTaxpayerChatStore.getInitialState(), true);
});

describe('TaxpayerChatPanel error path', () => {
  it('renders a role="alert" pane when the proxy returns a 5xx', async () => {
    server.use(
      http.post(
        '/api/chat',
        () => new HttpResponse('upstream unavailable', { status: 500 }),
      ),
    );

    render(
      <MemoryRouter initialEntries={['/taxpayers/stub-1/chat']}>
        <Routes>
          <Route path="/taxpayers/:id/chat" element={<TaxpayerChatPanel />} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/upstream unavailable/);
    // The failed turn was never persisted - onFinish only fires on success.
    expect(useTaxpayerChatStore.getState().messages).toHaveLength(0);
  });
});
