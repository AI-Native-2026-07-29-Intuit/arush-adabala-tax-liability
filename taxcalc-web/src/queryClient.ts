// src/queryClient.ts
import { QueryClient } from '@tanstack/react-query';

/**
 * Single TanStack Query client for the app's REST traffic (GraphQL stays on
 * Apollo's own cache - see `apollo/client.ts`). A one-minute `staleTime`
 * matches the backend's Redis read-through cache TTL, so a refetch inside
 * that window would just re-read the same cached row.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
