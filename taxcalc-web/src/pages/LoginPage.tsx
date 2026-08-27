// src/pages/LoginPage.tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setStoredJwt } from '../lib/jwtStorage';

// Not a real token - `router.tsx`'s ProtectedLayout only checks that
// *something* is present under uc:jwt, and the backend's own OAuth2
// resource server (a real IdP, not this app) would reject this at the
// API boundary. Good enough to exercise the client-side route guard
// without a login flow to build.
const STUB_JWT = 'stub.dev.token';

/**
 * Writes a stub JWT to localStorage and navigates to /taxpayers - stands in
 * for a real login flow. Email/password are real, labelled fields (so a
 * keyboard user or an E2E test has actual fields to fill, not a bare
 * button) but neither is validated against anything - there's no IdP here
 * to check credentials against, only the backend's own OAuth2 resource
 * server does that, at the API boundary, on a real request. The fields
 * exist to gate "sign in" on something having been typed, not to
 * authenticate; decorative-but-unenforced fields would be worse than no
 * fields at all.
 */
export function LoginPage(): React.ReactElement {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const signIn = (): void => {
    setStoredJwt(STUB_JWT);
    // react-router's data-router `navigate` returns a Promise (it resolves
    // once the destination route's loaders settle); nothing here needs to
    // wait on it, so `void` marks that deliberate rather than an oversight.
    void navigate('/taxpayers');
  };

  return (
    <main>
      <h1>Sign in</h1>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
      </label>
      <label>
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
      </label>
      <button type="button" onClick={signIn} disabled={email.trim() === '' || password.trim() === ''}>
        Sign in (stub)
      </button>
    </main>
  );
}
