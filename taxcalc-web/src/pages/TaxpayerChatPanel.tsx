// src/pages/TaxpayerChatPanel.tsx
import { useChat } from '@ai-sdk/react';
import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';

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
 */
export function TaxpayerChatPanel(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();

  const { messages, input, handleInputChange, handleSubmit, isLoading, stop, reload, error } =
    useChat({
      api: '/api/chat',
      id: `taxpayer-${id}`,
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
        <button type="button" onClick={() => reload()} disabled={isLoading}>
          Regenerate
        </button>
      </form>
    </section>
  );
}
