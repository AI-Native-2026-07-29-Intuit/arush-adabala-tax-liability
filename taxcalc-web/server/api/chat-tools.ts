// server/api/chat-tools.ts
import { tool } from 'ai';
import { z } from 'zod';

const REST_BASE = 'http://localhost:8080/api/v1/taxpayers';

/**
 * Tools the streaming assistant in `server/api/chat.ts` can call back into
 * this app's own data layer with, rather than answering from the model's
 * (untrustworthy, potentially stale) training data. Both hit the same W3
 * D2 REST backend `hooks/useGetTaxLiabilityRest.ts` already calls from the
 * browser - the difference here is these calls happen server-side, inside
 * the tool's `execute`, never exposing the REST backend to the browser
 * through this route.
 */
export const taxpayerTools = {
  lookupTaxpayer: tool({
    description:
      'Look up a single taxpayer by id. Returns the canonical record stored in the W3 D2 REST backend.',
    parameters: z.object({ id: z.string() }),
    execute: async ({ id }: { id: string }) => {
      const res = await fetch(`${REST_BASE}/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },
  }),
  estimateLiability: tool({
    description:
      'Search the taxpayer corpus by year. Returns a small array the assistant can quote inline.',
    parameters: z.object({ year: z.number() }),
    execute: async ({ year }: { year: number }) => {
      const res = await fetch(`${REST_BASE}?year=${year}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    },
  }),
};
