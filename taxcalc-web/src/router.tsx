// src/router.tsx
import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import { getStoredJwt } from './lib/jwtStorage';
import { LoginPage } from './pages/LoginPage';
import { TaxpayerListPage } from './pages/TaxpayerListPage';
import { TaxpayerDetailPage } from './pages/TaxpayerDetailPage';
import { TaxpayerSummaryPage } from './pages/TaxpayerSummaryPage';

/**
 * Client-side route guard: redirects to `/login` when `uc:jwt` is absent
 * from localStorage, otherwise renders its child routes via `Outlet`. Pure
 * presence check, not validation - the backend's own OAuth2 resource
 * server is what actually rejects an invalid or expired token.
 */
export function ProtectedLayout(): React.ReactElement {
  const jwt = getStoredJwt();
  if (jwt === null) return <Navigate to="/login" replace />;
  return <Outlet />;
}

/**
 * Top-level route table, replacing the W4 D1/D2 hash-routing placeholder
 * in `App.tsx`. Every `/taxpayers*` route sits behind {@link
 * ProtectedLayout}; `/login` is the only unauthenticated route.
 */
export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <ProtectedLayout />,
    children: [
      { path: '/', element: <Navigate to="/taxpayers" replace /> },
      { path: '/taxpayers', element: <TaxpayerListPage /> },
      { path: '/taxpayers/:id', element: <TaxpayerDetailPage /> },
      { path: '/taxpayers/:id/summary', element: <TaxpayerSummaryPage /> },
    ],
  },
]);
