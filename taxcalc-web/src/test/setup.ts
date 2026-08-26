import '@testing-library/jest-dom';
import { expect } from 'vitest';
import { toHaveNoViolations } from 'jest-axe';
import './server'; // installs MSW's request handlers for every test file

// jest-axe's `toHaveNoViolations` export is already the matcher-map shape
// expect.extend wants ({ toHaveNoViolations: fn }), not a bare function -
// `expect.extend({ toHaveNoViolations })` would wrap it one level too deep
// and register a matcher whose "function" is actually an object, which
// vitest's expect then fails to call at all.
expect.extend(toHaveNoViolations);

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
