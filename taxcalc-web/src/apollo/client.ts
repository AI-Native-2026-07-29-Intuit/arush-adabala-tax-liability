// src/apollo/client.ts
import { ApolloClient, HttpLink, InMemoryCache } from '@apollo/client';
import { setContext } from '@apollo/client/link/context';

// THREAT MODEL: storing the JWT in localStorage exposes it to any XSS that
// runs on the page. Accepted for now because the W6 cookie story (HttpOnly,
// SameSite=Strict, server-set) isn't built yet - see the router's
// ProtectedLayout, which reads the same key.
const JWT_STORAGE_KEY = 'uc:jwt';

const httpLink = new HttpLink({ uri: 'http://localhost:8080/graphql' });

const authLink = setContext((_operation, { headers }) => {
  const token = localStorage.getItem(JWT_STORAGE_KEY);
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
