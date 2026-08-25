import '@testing-library/jest-dom';
import './server'; // installs MSW's request handlers for every test file

// jsdom doesn't implement Element.scrollIntoView (layout is entirely
// unmeasured under jsdom, so there's nothing meaningful to scroll to) -
// TaxpayerChatPanel's auto-scroll-on-new-message effect calls it
// unconditionally, so every test rendering that component needs a stub or
// the effect throws.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
