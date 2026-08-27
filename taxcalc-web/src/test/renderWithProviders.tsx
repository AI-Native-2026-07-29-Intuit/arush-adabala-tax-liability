// src/test/renderWithProviders.tsx
import type { ReactElement } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ApolloProvider, type ApolloClient, type NormalizedCacheObject } from '@apollo/client';
import { MockedProvider, type MockedResponse } from '@apollo/client/testing';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

interface ProviderOptions {
  /** Initial route(s) for the `MemoryRouter` - defaults to the app root. */
  readonly initialEntries?: readonly string[];
  /**
   * Canned GraphQL responses for Apollo's `MockedProvider` - the default
   * Apollo test double, matching each outgoing operation by query +
   * variables rather than hitting a real network layer. This is what every
   * Task 1 component test in `src/pages/*.test.tsx` uses.
   */
  readonly mocks?: ReadonlyArray<MockedResponse>;
  /**
   * Escape hatch for tests that need a real `ApolloClient` over `HttpLink`
   * intercepted by MSW instead - e.g. the Task 2 integration tests, which
   * are specifically exercising real network/cache behavior (a genuine
   * cache-hit render, `server.use()` overrides mid-test) that
   * `MockedProvider`'s per-call mock matching can't express. Passing this
   * overrides `mocks` entirely.
   */
  readonly apolloClient?: ApolloClient<NormalizedCacheObject>;
  /** A pre-built QueryClient, for tests that need to assert against its cache directly. */
  readonly queryClient?: QueryClient;
}

interface RenderWithProvidersResult extends RenderResult {
  readonly user: UserEvent;
  readonly queryClient: QueryClient;
}

function newQueryClient(): QueryClient {
  // MSW backs the network call for every test, so retries only slow a
  // failing assertion down; gcTime: 0 keeps one test's cached result from
  // leaking into the next render within the same file.
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

/**
 * Mounts `ui` inside `MemoryRouter` + `QueryClientProvider` + an Apollo
 * provider - the three providers every routed page under `src/pages/`
 * needs - and returns a single `userEvent.setup()` instance alongside the
 * render utils and the QueryClient, so tests can assert against REST cache
 * state without threading their own provider boilerplate through every
 * file. The Apollo layer defaults to `MockedProvider` (pass `mocks`); pass
 * `apolloClient` instead for a real client over MSW.
 */
export function renderWithProviders(
  ui: ReactElement,
  opts: ProviderOptions = {},
): RenderWithProvidersResult {
  const { initialEntries = ['/'], mocks = [], apolloClient, queryClient = newQueryClient() } = opts;

  const user = userEvent.setup();
  const apolloLayer = apolloClient ? (
    <ApolloProvider client={apolloClient}>
      <MemoryRouter initialEntries={[...initialEntries]}>{ui}</MemoryRouter>
    </ApolloProvider>
  ) : (
    <MockedProvider mocks={[...mocks]}>
      <MemoryRouter initialEntries={[...initialEntries]}>{ui}</MemoryRouter>
    </MockedProvider>
  );

  const utils = render(<QueryClientProvider client={queryClient}>{apolloLayer}</QueryClientProvider>);

  return { user, queryClient, ...utils };
}
