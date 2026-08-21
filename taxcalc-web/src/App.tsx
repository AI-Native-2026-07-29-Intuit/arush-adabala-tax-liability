// src/App.tsx
//
// TanStack Router replaces this hash check on W4 D3; for D1 the only route
// this app knows is #/taxpayers/stub-id-1.
import { useEffect, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { TaxpayerDetailPage } from './pages/TaxpayerDetailPage';

const STUB_ROUTE = '#/taxpayers/stub-id-1';

export function App(): React.ReactElement {
  const [hash, setHash] = useState<string>(window.location.hash);

  useEffect(() => {
    const onHashChange = (): void => setHash(window.location.hash);
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  if (hash !== STUB_ROUTE) {
    return <p>go to {STUB_ROUTE}</p>;
  }

  return (
    <ErrorBoundary
      fallback={(error, reset) => (
        <div role="alert" className="error-card">
          <h2>Something went wrong</h2>
          <pre>{error.message}</pre>
          <button onClick={reset}>Try again</button>
        </div>
      )}
    >
      <TaxpayerDetailPage></TaxpayerDetailPage>
    </ErrorBoundary>
  );
}

export default App;
