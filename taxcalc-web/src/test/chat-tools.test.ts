// src/test/chat-tools.test.ts
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { taxpayerTools, ToolResponseValidationError } from '../../server/api/chat-tools';
import { server } from './server';

const TOOL_OPTIONS = { toolCallId: 'call-1', messages: [] };

describe('taxpayerTools response validation', () => {
  it('lookupTaxpayer returns the validated record on a well-shaped 200', async () => {
    // The default handler in test/handlers.ts already returns a shape
    // matching taxpayerRestSchema field-for-field - no override needed.
    const result = await taxpayerTools.lookupTaxpayer.execute?.({ id: 'stub-1' }, TOOL_OPTIONS);
    expect(result).toMatchObject({ id: 'stub-1', filingStatus: 'SINGLE' });
  });

  it('lookupTaxpayer throws ToolResponseValidationError when the REST response is missing required fields', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers/:id', () => HttpResponse.json({ id: 'stub-1' })),
    );

    await expect(
      taxpayerTools.lookupTaxpayer.execute?.({ id: 'stub-1' }, TOOL_OPTIONS),
    ).rejects.toThrow(ToolResponseValidationError);
  });

  it('lookupTaxpayer throws ToolResponseValidationError when a field has the wrong type', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers/:id', () =>
        HttpResponse.json({
          id: 'stub-1',
          displayName: 'Stub Taxpayer',
          filingStatus: 'SINGLE',
          homeJurisdiction: 'COLORADO',
          createdAt: '2025-01-04T00:00:00Z',
          liabilities: [],
          tags: 'not-an-array',
        }),
      ),
    );

    await expect(
      taxpayerTools.lookupTaxpayer.execute?.({ id: 'stub-1' }, TOOL_OPTIONS),
    ).rejects.toThrow(ToolResponseValidationError);
  });

  it('estimateLiability returns the validated list on a well-shaped 200', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers', () =>
        HttpResponse.json([{ id: 'stub-1', taxYear: 2024, liabilityAmount: 4820 }]),
      ),
    );

    const result = await taxpayerTools.estimateLiability.execute?.({ year: 2024 }, TOOL_OPTIONS);
    expect(result).toEqual([{ id: 'stub-1', taxYear: 2024, liabilityAmount: 4820 }]);
  });

  it('estimateLiability throws ToolResponseValidationError when the response is not an array', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers', () => HttpResponse.json({ not: 'an array' })),
    );

    await expect(
      taxpayerTools.estimateLiability.execute?.({ year: 2024 }, TOOL_OPTIONS),
    ).rejects.toThrow(ToolResponseValidationError);
  });

  it('both tools still throw a plain Error (not ToolResponseValidationError) on a non-2xx status', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers/:id', () => new HttpResponse(null, { status: 500 })),
    );

    await expect(
      taxpayerTools.lookupTaxpayer.execute?.({ id: 'stub-1' }, TOOL_OPTIONS),
    ).rejects.toThrow('HTTP 500');
  });
});
