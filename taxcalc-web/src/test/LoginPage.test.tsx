// src/test/LoginPage.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

describe('LoginPage', () => {
  it('clicking "Sign in (stub)" stores a JWT and navigates to /taxpayers', () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage></LoginPage>} />
          <Route path="/taxpayers" element={<p>taxpayers page</p>} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sign in (stub)' }));

    expect(screen.getByText('taxpayers page')).toBeInTheDocument();
    expect(getStoredJwt()).not.toBeNull();
  });
});
