// src/pages/LoginPage.tsx
import { useNavigate } from 'react-router-dom';
import { setStoredJwt } from '../lib/jwtStorage';

// Not a real token - `router.tsx`'s ProtectedLayout only checks that
// *something* is present under uc:jwt, and the backend's own OAuth2
// resource server (a real IdP, not this app) would reject this at the
// API boundary. Good enough to exercise the client-side route guard
// without a login flow to build.
const STUB_JWT = 'stub.dev.token';

/** Writes a stub JWT to localStorage and navigates to /taxpayers - stands in for a real login flow. */
export function LoginPage(): React.ReactElement {
  const navigate = useNavigate();

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
      <button type="button" onClick={signIn}>
        Sign in (stub)
      </button>
    </main>
  );
}
