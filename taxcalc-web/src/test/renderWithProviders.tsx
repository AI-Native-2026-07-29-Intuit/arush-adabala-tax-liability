// src/test/renderWithProviders.tsx
import type { ReactElement } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ApolloClient, ApolloProvider, HttpLink, InMemoryCache, type NormalizedCacheObject } from '@apollo/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

interface ProviderOptions {
  /** Initial route(s) for the `MemoryRouter` - defaults to the app root. */
  readonly initialEntries?: readonly string[];
  /** A pre-built Apollo client, for tests that need to assert against its cache directly. */
  readonly apolloClient?: ApolloClient<NormalizedCacheObject>;
  /** A pre-built QueryClient, for tests that need to assert against its cache directly. */
  readonly queryClient?: QueryClient;
}

interface RenderWithProvidersResult extends RenderResult {
  readonly user: UserEvent;
  readonly queryClient: QueryClient;
  readonly apolloClient: ApolloClient<NormalizedCacheObject>;
}

function newApolloClient(): ApolloClient<NormalizedCacheObject> {
  return new ApolloClient({
    link: new HttpLink({ uri: 'http://localhost:8080/graphql' }),
    cache: new InMemoryCache(),
  });
}

function newQueryClient(): QueryClient {
  // MSW backs the network call for every test, so retries only slow a
  // failing assertion down; gcTime: 0 keeps one test's cached result from
  // leaking into the next render within the same file.
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

/**
 * Mounts `ui` inside `MemoryRouter` + `QueryClientProvider` + `ApolloProvider`
 * - the three providers every routed page under `src/pages/` needs - and
 * returns a single `userEvent.setup()` instance alongside the render utils
 * and both clients, so tests can assert against cache state without
 * threading their own provider boilerplate through every file.
 */
export function renderWithProviders(
  ui: ReactElement,
  opts: ProviderOptions = {},
): RenderWithProvidersResult {
  const { initialEntries = ['/'], apolloClient = newApolloClient(), queryClient = newQueryClient() } = opts;

  const user = userEvent.setup();
  const utils = render(
    <ApolloProvider client={apolloClient}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[...initialEntries]}>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ApolloProvider>,
  );

  return { user, queryClient, apolloClient, ...utils };
}
