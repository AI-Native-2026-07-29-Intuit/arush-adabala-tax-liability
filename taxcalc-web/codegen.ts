// codegen.ts
//
// `schema` points at the backend's checked-in SDL file rather than
// introspecting a running `http://localhost:8080/graphql` server: Docker
// wasn't available while building this, and pointing at the file the
// backend already publishes from `src/main/resources/graphql/schema.graphqls`
// makes codegen reproducible without a live container. Swap this for the
// introspection URL once local-only CI needs to catch server/schema drift.
import type { CodegenConfig } from '@graphql-codegen/cli';

const config: CodegenConfig = {
  schema: '../src/main/resources/graphql/schema.graphqls',
  documents: './src/queries/**/*.graphql',
  generates: {
    'src/gql/generated/': {
      preset: 'client',
      // tsconfig.json turns on verbatimModuleSyntax; without this the
      // preset emits plain `import` statements for type-only names and
      // `tsc` rejects them.
      config: { useTypeImports: true },
    },
    // The client preset above deliberately doesn't generate named hooks
    // (Apollo's own useQuery/useMutation infer everything they need from
    // a TypedDocumentNode) - but pages here call useLatestTaxpayersQuery
    // and useSummarizeTaxpayerMutation directly, so a second output using
    // the classic typescript/typescript-operations/typescript-react-apollo
    // plugin trio generates those alongside the client preset's own files.
    'src/gql/generated/hooks.ts': {
      plugins: ['typescript', 'typescript-operations', 'typescript-react-apollo'],
      config: { useTypeImports: true, withHooks: true },
    },
  },
};

export default config;
