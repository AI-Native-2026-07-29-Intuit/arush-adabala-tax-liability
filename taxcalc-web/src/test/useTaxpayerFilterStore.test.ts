// src/test/useTaxpayerFilterStore.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';

beforeEach(() => {
  // Zustand v5: reset the store to its initial state before each test so
  // tests do not bleed into each other.
  useTaxpayerFilterStore.setState(useTaxpayerFilterStore.getInitialState(), true);
});

describe('useTaxpayerFilterStore', () => {
  it('setSearchText updates state.searchText', () => {
    useTaxpayerFilterStore.getState().setSearchText('foo');
    expect(useTaxpayerFilterStore.getState().searchText).toBe('foo');
  });

  it('setThreshold updates state.threshold', () => {
    useTaxpayerFilterStore.getState().setThreshold(80);
    expect(useTaxpayerFilterStore.getState().threshold).toBe(80);
  });

  it('setFilingStatusFilter is last-write-wins', () => {
    useTaxpayerFilterStore.getState().setFilingStatusFilter(['A', 'B']);
    useTaxpayerFilterStore.getState().setFilingStatusFilter(['C']);
    expect(useTaxpayerFilterStore.getState().filingStatusFilter).toEqual(['C']);
  });

  it('reset() returns state to the initial shape', () => {
    useTaxpayerFilterStore.getState().setSearchText('foo');
    useTaxpayerFilterStore.getState().setThreshold(80);
    useTaxpayerFilterStore.getState().reset();
    expect(useTaxpayerFilterStore.getState().searchText).toBe('');
    expect(useTaxpayerFilterStore.getState().threshold).toBe(50);
  });
});
