// src/apollo/client.ts
import { ApolloClient, HttpLink, InMemoryCache } from '@apollo/client';
import { setContext } from '@apollo/client/link/context';
import { getStoredJwt } from '../lib/jwtStorage';

// THREAT MODEL: storing the JWT in localStorage exposes it to any XSS that
// runs on the page. Accepted for now because the W6 cookie story (HttpOnly,
// SameSite=Strict, server-set) isn't built yet - see the router's
// ProtectedLayout, which reads the same key via jwtStorage.ts.
const httpLink = new HttpLink({ uri: 'http://localhost:8080/graphql' });

// Apollo's own `DefaultContext` types `headers` as `Record<string, any>` -
// this narrower annotation on the destructured parameter (not a cast on
// its use) is what gives the spread below a real type instead of `any`.
const authLink = setContext((_operation, { headers }: { headers?: Record<string, string> }) => {
  const token = getStoredJwt();
  return {
    headers: {
      ...headers,
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
  };
});

/**
 * Apollo Client singleton for the app's GraphQL traffic. `InMemoryCache`
 * normalizes `Taxpayer` records by `id` so `latestTaxpayers` (the list
 * query) and any future single-taxpayer query share one cache entry per
 * record instead of duplicating it.
 */
export const apolloClient = new ApolloClient({
  link: authLink.concat(httpLink),
  cache: new InMemoryCache({
    typePolicies: {
      Taxpayer: { keyFields: ['id'] },
    },
  }),
});
