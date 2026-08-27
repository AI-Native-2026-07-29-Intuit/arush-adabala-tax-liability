// src/test/LoginPage.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LoginPage } from '../pages/LoginPage';
import { getStoredJwt } from '../lib/jwtStorage';

// Same jsdom/Node localStorage gap ProtectedLayout.test.tsx documents:
// window.localStorage comes back undefined under this Vitest environment,
// so setStoredJwt's write would otherwise be silently swallowed by its
// own try/catch and this test could never observe it.
function installFakeLocalStorage(): void {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
  });
}

beforeEach(() => {
  installFakeLocalStorage();
});

function renderLoginPage(): void {
  render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage></LoginPage>} />
        <Route path="/taxpayers" element={<p>taxpayers page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LoginPage', () => {
  it('the Sign in button is disabled until both email and password are filled', async () => {
    const user = userEvent.setup();
    renderLoginPage();

    const button = screen.getByRole('button', { name: 'Sign in (stub)' });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText('Email'), 'engineer@uptimecrew.example.internal');
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText('Password'), 'synthetic-test-pwd');
    expect(button).toBeEnabled();
  });

  it('filling both fields and clicking "Sign in (stub)" stores a JWT and navigates to /taxpayers', async () => {
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText('Email'), 'engineer@uptimecrew.example.internal');
    await user.type(screen.getByLabelText('Password'), 'synthetic-test-pwd');
    await user.click(screen.getByRole('button', { name: 'Sign in (stub)' }));

    expect(screen.getByText('taxpayers page')).toBeInTheDocument();
    expect(getStoredJwt()).not.toBeNull();
  });
});
