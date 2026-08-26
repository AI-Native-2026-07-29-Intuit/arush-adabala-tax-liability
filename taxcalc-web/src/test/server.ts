// src/test/server.ts
import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/**
 * The MSW server instance every test file imports to call `server.use(...)`
 * for a one-off handler override. Its `listen`/`resetHandlers`/`close`
 * lifecycle (plus the AbortController work-around it needs) is bound in
 * `setupTests.ts`, not here, so every test file's setup runs through one
 * place.
 */
export const server = setupServer(...handlers);
