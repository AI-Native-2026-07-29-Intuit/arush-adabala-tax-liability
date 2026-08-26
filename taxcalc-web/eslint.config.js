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
      // `@typescript-eslint/no-explicit-any` only catches `any` used as a
      // type position (`: any`, `Array<any>`); it doesn't catch `as any`,
      // a type *assertion* rather than an annotation - this closes that
      // gap so both routes to "I told the compiler to stop checking" are
      // banned the same way.
      'no-restricted-syntax': [
        'error',
        {
          selector: "TSTypeAssertion[typeAnnotation.typeName.name='any'], TSAsExpression[typeAnnotation.typeName.name='any']",
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
