// server/api/chat-tools.ts
import { tool } from 'ai';
import { z } from 'zod';

const REST_BASE = 'http://localhost:8080/api/v1/taxpayers';

/**
 * Thrown when a REST call inside a tool's `execute` returns `200 OK` with a
 * body that doesn't match the expected shape - a different failure class
 * from a non-2xx status (already handled by the plain `Error` thrown
 * below): the backend answered, but answered with something this proxy
 * can't safely hand to the model as fact. `streamText` wraps whatever an
 * `execute` throws in a `ToolExecutionError` with this as `.cause` (see
 * `ai`'s `executeTools`), which `chat.ts`'s `toClientErrorMessage` unwraps
 * to surface a message specific to "the response was malformed" rather
 * than the generic "temporarily unavailable" used for connectivity
 * failures - the same "log the real cause, return something narrower and
 * safe" shape `UpstreamStatusError` already uses for the streaming path.
 */
export class ToolResponseValidationError extends Error {
  constructor(toolName: string, issues: z.ZodIssue[]) {
    super(`${toolName}'s REST response didn't match the expected shape.`);
    this.name = 'ToolResponseValidationError';
    console.error(`chat-tools: ${toolName} response failed validation`, issues);
  }
}

/**
 * Mirrors `src/hooks/useGetTaxLiabilityRest.ts`'s `TaxpayerRest`/
 * `EmbeddedLiabilityRest` types field-for-field - that hook already
 * documents this as the real, observed shape of `GET
 * /api/v1/taxpayers/{id}`. Not imported directly (that hook's module also
 * pulls in `@tanstack/react-query`, a browser-side dependency this
 * server-side file has no business importing), so keep the two in sync by
 * hand if `TaxpayerReadModel`'s JSON shape ever changes.
 */
const embeddedLiabilityRestSchema = z.object({
  taxYear: z.number(),
  bracketId: z.string(),
  taxableAmount: z.number(),
  liabilityAmount: z.number(),
  computedAt: z.string(),
});

const taxpayerRestSchema = z.object({
  id: z.string(),
  displayName: z.string(),
  filingStatus: z.string(),
  homeJurisdiction: z.string(),
  createdAt: z.string(),
  liabilities: z.array(embeddedLiabilityRestSchema),
  tags: z.array(z.string()),
});

/**
 * Unlike `taxpayerRestSchema` above, there's no existing hook or backend
 * controller to mirror here: `GET /api/v1/taxpayers?year=` isn't
 * implemented on the real W3 D2 backend at all (only `GET /{id}` is) - see
 * the root README's Week 4 Day 4 section. This schema formalizes
 * `dev/stub-spring-ai.ts`'s own canned shape, which is the only thing that
 * has ever actually answered this call; it's unverified against a real
 * backend and should be revisited once one exists.
 */
const liabilityEstimateListSchema = z.array(
  z.object({
    id: z.string(),
    taxYear: z.number(),
    liabilityAmount: z.number(),
  }),
);

/**
 * Tools the streaming assistant in `server/api/chat.ts` can call back into
 * this app's own data layer with, rather than answering from the model's
 * (untrustworthy, potentially stale) training data. Both hit the same W3
 * D2 REST backend `hooks/useGetTaxLiabilityRest.ts` already calls from the
 * browser - the difference here is these calls happen server-side, inside
 * the tool's `execute`, never exposing the REST backend to the browser
 * through this route. Each response is validated before being returned as
 * the tool result: a non-2xx status throws a plain `Error` (an
 * unreachable/misbehaving backend), while a 2xx with the wrong shape
 * throws `ToolResponseValidationError` (a reachable but untrustworthy
 * one) - two different failure classes worth telling apart in the logs,
 * even though both end up surfacing to the client the same way.
 */
export const taxpayerTools = {
  lookupTaxpayer: tool({
    description:
      'Look up a single taxpayer by id. Returns the canonical record stored in the W3 D2 REST backend.',
    parameters: z.object({ id: z.string() }),
    execute: async ({ id }: { id: string }) => {
      const res = await fetch(`${REST_BASE}/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const parsed = taxpayerRestSchema.safeParse(await res.json());
      if (!parsed.success) {
        throw new ToolResponseValidationError('lookupTaxpayer', parsed.error.issues);
      }
      return parsed.data;
    },
  }),
  estimateLiability: tool({
    description:
      'Search the taxpayer corpus by year. Returns a small array the assistant can quote inline.',
    parameters: z.object({ year: z.number() }),
    execute: async ({ year }: { year: number }) => {
      const res = await fetch(`${REST_BASE}?year=${year}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const parsed = liabilityEstimateListSchema.safeParse(await res.json());
      if (!parsed.success) {
        throw new ToolResponseValidationError('estimateLiability', parsed.error.issues);
      }
      return parsed.data;
    },
  }),
};
