// src/test/TaxpayerDetailPage.reducer.test.ts
import { describe, it, expect } from 'vitest';
import {
  detailReducer,
  INITIAL_DETAIL_STATE,
  type DetailState,
} from '../pages/TaxpayerDetailPage.reducer';

const SAMPLE = {
  id: 'stub-id-1',
  filingStatus: 'SINGLE',
  jurisdictionCount: 3,
  totalLiability: '8420.00',
  lines: [],
} as const;

describe('TaxpayerDetailPage.reducer', () => {
  it('idle + fetch/start → loading', () => {
    expect(detailReducer(INITIAL_DETAIL_STATE, { type: 'fetch/start' })).toEqual({
      status: 'loading',
    });
  });

  it('loading + fetch/success(entity) → success(entity)', () => {
    const loading: DetailState = { status: 'loading' };
    expect(detailReducer(loading, { type: 'fetch/success', payload: SAMPLE })).toEqual({
      status: 'success',
      data: SAMPLE,
    });
  });

  it('loading + fetch/success(null) → empty', () => {
    const loading: DetailState = { status: 'loading' };
    expect(detailReducer(loading, { type: 'fetch/success', payload: null })).toEqual({
      status: 'empty',
    });
  });

  it('loading + fetch/error → error with message', () => {
    const loading: DetailState = { status: 'loading' };
    expect(detailReducer(loading, { type: 'fetch/error', error: 'boom' })).toEqual({
      status: 'error',
      error: 'boom',
    });
  });

  it('any + reset → idle', () => {
    const success: DetailState = { status: 'success', data: SAMPLE };
    expect(detailReducer(success, { type: 'reset' })).toEqual(INITIAL_DETAIL_STATE);
  });
});
