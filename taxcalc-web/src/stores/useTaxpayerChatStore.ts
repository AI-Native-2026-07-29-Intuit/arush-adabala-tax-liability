// src/stores/useTaxpayerChatStore.ts
import type { Message } from 'ai';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { safeLocalStorage } from '../lib/safeLocalStorage';

interface TaxpayerChatStoreState {
  readonly messages: readonly Message[];
  readonly appendAssistantMessage: (message: Message) => void;
  readonly clear: () => void;
}

/**
 * Persists completed chat turns to `localStorage` under `uc:taxpayer-chat`
 * so a page reload rehydrates the transcript instead of starting blank.
 * `appendAssistantMessage` must only ever be called from `useChat`'s
 * `onFinish` callback, never from a per-token stream callback (e.g.
 * `onMessageStream`) - writing on every token would fire this store's
 * `persist` middleware dozens of times a second, tanking render FPS while
 * streaming, and would let a page reload mid-stream rehydrate a message
 * that never actually finished.
 *
 * Uses `safeLocalStorage` (not `persist`'s own `window.localStorage`
 * default) for the same reason `useTaxpayerFilterStore` does: under this
 * Node/jsdom/Vitest combination `window.localStorage` is genuinely
 * `undefined`, which would otherwise leave `persist` silently unable to
 * write - not merely untested, but genuinely broken in dev too, since
 * nothing else here would surface the gap.
 */
export const useTaxpayerChatStore = create<TaxpayerChatStoreState>()(
  persist(
    (set) => ({
      messages: [],
      appendAssistantMessage: (message) =>
        set((state) => ({ messages: [...state.messages, message] })),
      clear: () => set({ messages: [] }),
    }),
    { name: 'uc:taxpayer-chat', storage: createJSONStorage(() => safeLocalStorage) },
  ),
);
