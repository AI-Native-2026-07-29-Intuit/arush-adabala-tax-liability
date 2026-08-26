import '@testing-library/jest-dom';
import { expect } from 'vitest';
import { toHaveNoViolations } from 'jest-axe';
import './server'; // installs MSW's request handlers for every test file

// jest-axe's matcher isn't in @testing-library/jest-dom, so it needs its
// own expect.extend + Assertion<T> module augmentation (declared below) for
// TypeScript to know `toHaveNoViolations()` exists on the matcher chain.
expect.extend({ toHaveNoViolations });

declare module 'vitest' {
  interface Assertion<T> {
    toHaveNoViolations(): T;
  }
}

// jsdom doesn't implement Element.scrollIntoView (layout is entirely
// unmeasured under jsdom, so there's nothing meaningful to scroll to) -
// TaxpayerChatPanel's auto-scroll-on-new-message effect calls it
// unconditionally, so every test rendering that component needs a stub or
// the effect throws.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
