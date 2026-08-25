// src/pages/ToolCallCard.tsx
import type { ToolInvocation } from 'ai';

interface ToolCallCardProps {
  readonly invocation: ToolInvocation;
}

/**
 * Presentational card for one {@link ToolInvocation} on an assistant
 * message, rendered inline by `TaxpayerChatPanel` beneath the message text.
 * `invocation.state` walks `partial-call` (args still streaming in) →
 * `call` (args complete, tool executing server-side) → `result`; only the
 * last of those carries a `result` to render, so the result `<pre>` is
 * conditional rather than always present with a placeholder.
 */
export function ToolCallCard({ invocation }: ToolCallCardProps): React.ReactElement {
  return (
    <aside aria-label="tool-call" data-tool={invocation.toolName} data-state={invocation.state}>
      <header>
        called <code>{invocation.toolName}</code>
      </header>
      <pre>{JSON.stringify(invocation.args, null, 2)}</pre>
      {invocation.state === 'result' && (
        <pre data-testid="tool-result">{JSON.stringify(invocation.result, null, 2)}</pre>
      )}
    </aside>
  );
}
