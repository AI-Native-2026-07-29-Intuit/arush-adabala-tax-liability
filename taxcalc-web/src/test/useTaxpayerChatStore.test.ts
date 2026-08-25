// src/test/useTaxpayerChatStore.test.ts
import { waitFor } from '@testing-library/react';
import type { Message } from 'ai';
import { beforeEach, describe, expect, it } from 'vitest';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { safeLocalStorage } from '../lib/safeLocalStorage';
import { useTaxpayerChatStore } from '../stores/useTaxpayerChatStore';

const ASSISTANT_MESSAGE: Message = {
  id: 'msg-1',
  role: 'assistant',
  content: 'stub taxpayer reply.',
};

beforeEach(() => {
  // Resetting via setState (not window.localStorage.clear() - it's
  // undefined under this Node/jsdom/Vitest combination, see
  // src/lib/safeLocalStorage.ts) also re-triggers the persist middleware's
  // own write, so the storage layer resets along with the live state.
  useTaxpayerChatStore.setState(useTaxpayerChatStore.getInitialState(), true);
});

describe('useTaxpayerChatStore', () => {
  it('appendAssistantMessage adds to the messages array', () => {
    useTaxpayerChatStore.getState().appendAssistantMessage(ASSISTANT_MESSAGE);
    expect(useTaxpayerChatStore.getState().messages).toEqual([ASSISTANT_MESSAGE]);
  });

  it('clear() empties the messages array', () => {
    useTaxpayerChatStore.getState().appendAssistantMessage(ASSISTANT_MESSAGE);
    useTaxpayerChatStore.getState().clear();
    expect(useTaxpayerChatStore.getState().messages).toEqual([]);
  });

  it('persists to storage and rehydrates into a freshly created store instance', async () => {
    useTaxpayerChatStore.getState().appendAssistantMessage(ASSISTANT_MESSAGE);

    // The persist middleware's setItem() runs synchronously inside set(),
    // so the write is already in the storage layer here - no flush needed.
    expect(safeLocalStorage.getItem('uc:taxpayer-chat')).toContain('stub taxpayer reply.');

    // A second store built against the same `safeLocalStorage` singleton
    // stands in for a page reload: the store instance is brand new, but
    // the underlying storage (real localStorage in the browser; its
    // in-memory fallback here) persists independently of any one store's
    // lifetime. Hydration is async even for synchronous storage, so wait
    // on hasHydrated() rather than asserting immediately.
    const rehydratedStore = create<{ readonly messages: readonly Message[] }>()(
      persist(() => ({ messages: [] as readonly Message[] }), {
        name: 'uc:taxpayer-chat',
        storage: createJSONStorage(() => safeLocalStorage),
      }),
    );

    await waitFor(() => expect(rehydratedStore.persist.hasHydrated()).toBe(true));
    expect(rehydratedStore.getState().messages).toEqual([ASSISTANT_MESSAGE]);
  });
});
