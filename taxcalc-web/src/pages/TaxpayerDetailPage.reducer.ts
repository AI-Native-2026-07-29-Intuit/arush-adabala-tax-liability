// src/pages/TaxpayerDetailPage.reducer.ts
import type { Taxpayer } from '../types/taxpayer';

/** Discriminated-union state machine for {@link TaxpayerDetailPage}'s fetch lifecycle. */
export type DetailState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: Taxpayer }
  | { status: 'error'; error: string }
  | { status: 'empty' };

export type DetailAction =
  | { type: 'fetch/start' }
  | { type: 'fetch/success'; payload: Taxpayer | null }
  | { type: 'fetch/error'; error: string }
  | { type: 'reset' };

export const INITIAL_DETAIL_STATE: DetailState = { status: 'idle' };

/**
 * Pure reducer over {@link DetailState}. Lives in its own file, apart from
 * the page component, so it can be unit tested as a plain function.
 */
export function detailReducer(_state: DetailState, action: DetailAction): DetailState {
  switch (action.type) {
    case 'fetch/start':
      return { status: 'loading' };
    case 'fetch/success':
      return action.payload === null
        ? { status: 'empty' }
        : { status: 'success', data: action.payload };
    case 'fetch/error':
      return { status: 'error', error: action.error };
    case 'reset':
      return INITIAL_DETAIL_STATE;
    default: {
      // Exhaustiveness check: if a new action variant is added but a case
      // is missed above, this assignment fails to compile.
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}
