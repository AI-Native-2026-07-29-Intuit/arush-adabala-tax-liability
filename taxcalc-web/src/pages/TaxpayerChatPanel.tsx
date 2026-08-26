// src/pages/TaxpayerChatPanel.tsx
import { useChat } from '@ai-sdk/react';
import type { Message } from 'ai';
import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useTaxpayerChatStore } from '../stores/useTaxpayerChatStore';
import { ToolCallCard } from './ToolCallCard';

/**
 * Streaming chat assistant for a single taxpayer's route, replacing the W4
 * D3 one-shot `TaxpayerSummaryPage` mutation with a conversational UI built
 * on the Vercel AI SDK's `useChat`. `id: taxpayer-${id}` scopes each
 * taxpayer to its own message history (a distinct `useChat` instance, since
 * the hook keys its internal state off `id`); `api: '/api/chat'` targets
 * `vite.config.ts`'s dev-server proxy in front of the Hono `/api/chat`
 * route in `server/api/chat.ts`, which is the only place that ever talks to
 * the upstream model.
 *
 * Stop/Regenerate share `isLoading` for their disabled state (Stop is only
 * meaningful mid-stream; Regenerate would otherwise race a request already
 * in flight) rather than each tracking it independently. The transcript
 * auto-scrolls on every `messages` change - not just on finish - so a long
 * streaming reply keeps the newest tokens in view as they arrive instead of
 * only jumping once the message completes.
 *
 * `initialMessages` seeds `useChat` from `useTaxpayerChatStore`'s persisted
 * history so a page reload rehydrates the transcript instead of starting
 * blank; `onFinish` writes the completed assistant message back to that
 * store ONLY on completion, never per streamed token - see the store's own
 * doc comment for why that ordering matters. Tool invocations attached to
 * an assistant message (populated when the model calls `lookupTaxpayer` /
 * `estimateLiability` in `server/api/chat-tools.ts`) render inline as
 * `ToolCallCard`s beneath that message's text.
 */
export function TaxpayerChatPanel(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();
  const persistedMessages = useTaxpayerChatStore((s) => s.messages);
  const appendAssistantMessage = useTaxpayerChatStore((s) => s.appendAssistantMessage);

  const { messages, input, handleInputChange, handleSubmit, isLoading, stop, reload, error } =
    useChat({
      api: '/api/chat',
      id: `taxpayer-${id}`,
      initialMessages: persistedMessages as Message[],
      onFinish: (message) => appendAssistantMessage(message),
    });

  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <section aria-label="taxpayer-chat">
      <ul aria-label="chat-transcript">
        {messages.map((m) => (
          <li key={m.id} data-role={m.role}>
            <strong>{m.role}:</strong> {m.content}
            {(m.toolInvocations ?? []).map((invocation) => (
              <ToolCallCard key={invocation.toolCallId} invocation={invocation} />
            ))}
          </li>
        ))}
      </ul>
      <div ref={endRef} />

      {isLoading && <p role="status">Assistant is replying...</p>}
      {error && <p role="alert">Error: {error.message}</p>}

      <form onSubmit={handleSubmit}>
        <input
          aria-label="chat-input"
          value={input}
          onChange={handleInputChange}
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading || input.trim() === ''}>
          Send
        </button>
        <button type="button" onClick={stop} disabled={!isLoading}>
          Stop
        </button>
        <button type="button" onClick={() => void reload()} disabled={isLoading}>
          Regenerate
        </button>
      </form>
    </section>
  );
}
