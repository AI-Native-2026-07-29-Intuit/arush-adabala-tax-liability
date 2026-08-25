// src/pages/TaxpayerChatPanel.tsx
import { useChat } from '@ai-sdk/react';
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
 */
export function TaxpayerChatPanel(): React.ReactElement {
  const { id = '' } = useParams<{ id: string }>();

  const { messages, input, handleInputChange, handleSubmit, isLoading, error } = useChat({
    api: '/api/chat',
    id: `taxpayer-${id}`,
  });

  return (
    <section aria-label="taxpayer-chat">
      <ul aria-label="chat-transcript">
        {messages.map((m) => (
          <li key={m.id} data-role={m.role}>
            <strong>{m.role}:</strong> {m.content}
          </li>
        ))}
      </ul>

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
      </form>
    </section>
  );
}
