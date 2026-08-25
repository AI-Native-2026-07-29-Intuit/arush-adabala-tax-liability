// src/test/ProtectedLayout.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProtectedLayout } from '../router';
import { setStoredJwt } from '../lib/jwtStorage';

// jwtStorage.ts reads/writes via `window.localStorage`, which - as
// useTaxpayerFilterStore.ts's safeLocalStorage comment already documents -
// comes back `undefined` under this Node/jsdom/Vitest combination. A tiny
// Map-backed stand-in lets this file actually exercise the "jwt present"
// branch instead of jwtStorage.ts's try/catch silently swallowing every
// read and write.
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

function renderProtected(initialEntry: string): void {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/login" element={<p>login page</p>} />
        <Route element={<ProtectedLayout></ProtectedLayout>}>
          <Route path="/taxpayers" element={<p>taxpayers page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProtectedLayout', () => {
  it('redirects to /login when uc:jwt is missing', () => {
    renderProtected('/taxpayers');
    expect(screen.getByText('login page')).toBeInTheDocument();
  });

  it('renders the child route when uc:jwt is present', () => {
    setStoredJwt('stub.dev.token');
    renderProtected('/taxpayers');
    expect(screen.getByText('taxpayers page')).toBeInTheDocument();
  });
});
