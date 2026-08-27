// eslint.config.js — ESLint 9 flat config.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import jsxA11y from 'eslint-plugin-jsx-a11y';

export default tseslint.config(
  {
    ignores: [
      'dist',
      'node_modules',
      'coverage',
      'src/gql/generated',
      'playwright-report',
      'test-results',
      'e2e/.auth',
    ],
  },
  js.configs.recommended,
  // Type-checked rules (recommendedTypeChecked, not plain recommended) need
  // a real tsconfig to run against - languageOptions.parserOptions.project
  // below points at this project's own, which tsconfig.json's own
  // `include` already covers for every file glob this config lints.
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    settings: { react: { version: 'detect' } },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      'react/jsx-key': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      '@typescript-eslint/no-explicit-any': 'error',
      // A belt-and-suspenders ban on `as any`/`<any>x` alongside
      // `no-explicit-any` above (which already flags both in current
      // typescript-eslint versions - verified by hand, not assumed - so
      // this is deliberately redundant rather than covering a real gap).
      // `any` is a keyword type, not a named one: it parses to a
      // `TSAnyKeyword` node with no `typeName` property at all - that
      // property only exists on `TSTypeReference` nodes (`as Foo`, `as
      // Array<T>`). A selector checking `typeAnnotation.typeName.name`
      // therefore never matches `as any` specifically; `typeAnnotation.type`
      // is the check that actually works, confirmed with a scratch
      // `x as any` file before and after this fix.
      'no-restricted-syntax': [
        'error',
        {
          selector: "TSTypeAssertion[typeAnnotation.type='TSAnyKeyword'], TSAsExpression[typeAnnotation.type='TSAnyKeyword']",
          message: 'as any is banned; widen the type properly.',
        },
      ],
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  // recommendedTypeChecked's rules all require real type information, which
  // needs a tsconfig-backed parser project (set above, scoped to ts/tsx) -
  // this file itself is the one plain .js ESLint lints, so it turns those
  // rules back off for itself rather than trying to type-check its own config.
  {
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
);
