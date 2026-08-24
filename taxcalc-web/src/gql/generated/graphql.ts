/* eslint-disable */
/** Internal type. DO NOT USE DIRECTLY. */
type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
/** Internal type. DO NOT USE DIRECTLY. */
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
import type { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';
export type LatestTaxpayersQueryVariables = Exact<{
  limit?: number | null | undefined;
}>;


export type LatestTaxpayersQuery = { latestTaxpayers: Array<{ id: string, tags: Array<string>, lines: Array<{ id: string, description: string, amount: number }> }> };

export type SummarizeTaxpayerMutationVariables = Exact<{
  id: string | number;
}>;


export type SummarizeTaxpayerMutation = { summarizeTaxpayer: { __typename: 'TaxpayerSummary', filingStatus: string, totalLiability: number, jurisdictionCount: number, riskBand: string } };


export const LatestTaxpayersDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"LatestTaxpayers"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"limit"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"latestTaxpayers"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"Variable","name":{"kind":"Name","value":"limit"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"id"}},{"kind":"Field","name":{"kind":"Name","value":"tags"}},{"kind":"Field","name":{"kind":"Name","value":"lines"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"id"}},{"kind":"Field","name":{"kind":"Name","value":"description"}},{"kind":"Field","name":{"kind":"Name","value":"amount"}}]}}]}}]}}]} as unknown as DocumentNode<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>;
export const SummarizeTaxpayerDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"SummarizeTaxpayer"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"id"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"ID"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"summarizeTaxpayer"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"id"},"value":{"kind":"Variable","name":{"kind":"Name","value":"id"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"__typename"}},{"kind":"Field","name":{"kind":"Name","value":"filingStatus"}},{"kind":"Field","name":{"kind":"Name","value":"totalLiability"}},{"kind":"Field","name":{"kind":"Name","value":"jurisdictionCount"}},{"kind":"Field","name":{"kind":"Name","value":"riskBand"}}]}}]}}]} as unknown as DocumentNode<SummarizeTaxpayerMutation, SummarizeTaxpayerMutationVariables>;