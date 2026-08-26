// src/test/jest-axe.d.ts
//
// jest-axe ships no types of its own, and the published `@types/jest-axe`
// (last released for the 3.x line) types `toHaveNoViolations` against
// Jest's matcher signature, which doesn't structurally satisfy Vitest's
// `expect.extend` - so this declares just the two exports this project
// actually imports (setup.ts's `toHaveNoViolations`; any future caller of
// `axe` directly), typed against `axe-core`'s own (accurate) `AxeResults`.
declare module 'jest-axe' {
  import type { AxeResults, RunOptions } from 'axe-core';

  export function axe(html: Element | string, options?: RunOptions): Promise<AxeResults>;

  export function toHaveNoViolations(results: AxeResults): { pass: boolean; message: () => string };
}
