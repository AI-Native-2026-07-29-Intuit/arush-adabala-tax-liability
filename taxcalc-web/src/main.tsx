import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ApolloProvider } from '@apollo/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import { apolloClient } from './apollo/client';
import { queryClient } from './queryClient';
import { ErrorBoundary } from './components/ErrorBoundary';
import { router } from './router';

const rootElement = document.getElementById('root');
if (rootElement === null) {
  throw new Error('Root element #root not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <ApolloProvider client={apolloClient}>
      <QueryClientProvider client={queryClient}>
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
      </QueryClientProvider>
    </ApolloProvider>
  </StrictMode>,
);
