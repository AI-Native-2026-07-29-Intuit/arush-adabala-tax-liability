// src/App.tsx
import { RouterProvider } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import { router } from './router';

/** App root: renders the {@link router} route table, wrapped in an {@link ErrorBoundary}. */
export function App(): React.ReactElement {
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
      <RouterProvider router={router}></RouterProvider>
    </ErrorBoundary>
  );
}

export default App;
