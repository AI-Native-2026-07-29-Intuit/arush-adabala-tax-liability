// src/stores/useTaxpayerChatStore.ts
import type { Message } from 'ai';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

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
 */
export const useTaxpayerChatStore = create<TaxpayerChatStoreState>()(
  persist(
    (set) => ({
      messages: [],
      appendAssistantMessage: (message) =>
        set((state) => ({ messages: [...state.messages, message] })),
      clear: () => set({ messages: [] }),
    }),
    { name: 'uc:taxpayer-chat' },
  ),
);
