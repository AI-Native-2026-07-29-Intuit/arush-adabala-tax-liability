// src/test/ToolCallCard.test.tsx
import { render, screen } from '@testing-library/react';
import type { ToolInvocation } from 'ai';
import { describe, expect, it } from 'vitest';
import { ToolCallCard } from '../pages/ToolCallCard';

const PARTIAL_CALL: ToolInvocation = {
  state: 'partial-call',
  toolCallId: 'call-1',
  toolName: 'lookupTaxpayer',
  args: { id: 'stub' },
};

const CALL: ToolInvocation = {
  state: 'call',
  toolCallId: 'call-1',
  toolName: 'lookupTaxpayer',
  args: { id: 'stub-1' },
};

const RESULT: ToolInvocation = {
  state: 'result',
  toolCallId: 'call-1',
  toolName: 'lookupTaxpayer',
  args: { id: 'stub-1' },
  result: { id: 'stub-1', displayName: 'stub taxpayer' },
};

describe('ToolCallCard', () => {
  it('renders the partial-call state with its (possibly incomplete) args and no result', () => {
    render(<ToolCallCard invocation={PARTIAL_CALL} />);
    const card = screen.getByLabelText('tool-call');
    expect(card).toHaveAttribute('data-state', 'partial-call');
    expect(card).toHaveTextContent('lookupTaxpayer');
    expect(screen.queryByTestId('tool-result')).not.toBeInTheDocument();
  });

  it('renders the call state with complete args and no result', () => {
    render(<ToolCallCard invocation={CALL} />);
    const card = screen.getByLabelText('tool-call');
    expect(card).toHaveAttribute('data-state', 'call');
    expect(card).toHaveTextContent('"id": "stub-1"');
    expect(screen.queryByTestId('tool-result')).not.toBeInTheDocument();
  });

  it('renders the result state with the tool result payload', () => {
    render(<ToolCallCard invocation={RESULT} />);
    const card = screen.getByLabelText('tool-call');
    expect(card).toHaveAttribute('data-state', 'result');
    expect(screen.getByTestId('tool-result')).toHaveTextContent('stub taxpayer');
  });
});
