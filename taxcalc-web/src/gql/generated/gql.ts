/* eslint-disable */
import * as types from './graphql';
import type { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';

/**
 * Map of all GraphQL operations in the project.
 *
 * This map has several performance disadvantages:
 * 1. It is not tree-shakeable, so it will include all operations in the project.
 * 2. It is not minifiable, so the string of a GraphQL query will be multiple times inside the bundle.
 * 3. It does not support dead code elimination, so it will add unused operations.
 *
 * Therefore it is highly recommended to use the babel or swc plugin for production.
 * Learn more about it here: https://the-guild.dev/graphql/codegen/plugins/presets/preset-client#reducing-bundle-size
 */
type Documents = {
    "query LatestTaxpayers($limit: Int) {\n  latestTaxpayers(limit: $limit) {\n    id\n    tags\n    lines {\n      id\n      description\n      amount\n    }\n  }\n}": typeof types.LatestTaxpayersDocument,
    "mutation SummarizeTaxpayer($id: ID!) {\n  summarizeTaxpayer(id: $id) {\n    __typename\n    filingStatus\n    totalLiability\n    jurisdictionCount\n    riskBand\n  }\n}": typeof types.SummarizeTaxpayerDocument,
};
const documents: Documents = {
    "query LatestTaxpayers($limit: Int) {\n  latestTaxpayers(limit: $limit) {\n    id\n    tags\n    lines {\n      id\n      description\n      amount\n    }\n  }\n}": types.LatestTaxpayersDocument,
    "mutation SummarizeTaxpayer($id: ID!) {\n  summarizeTaxpayer(id: $id) {\n    __typename\n    filingStatus\n    totalLiability\n    jurisdictionCount\n    riskBand\n  }\n}": types.SummarizeTaxpayerDocument,
};

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 *
 *
 * @example
 * ```ts
 * const query = graphql(`query GetUser($id: ID!) { user(id: $id) { name } }`);
 * ```
 *
 * The query argument is unknown!
 * Please regenerate the types.
 */
export function graphql(source: string): unknown;

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "query LatestTaxpayers($limit: Int) {\n  latestTaxpayers(limit: $limit) {\n    id\n    tags\n    lines {\n      id\n      description\n      amount\n    }\n  }\n}"): (typeof documents)["query LatestTaxpayers($limit: Int) {\n  latestTaxpayers(limit: $limit) {\n    id\n    tags\n    lines {\n      id\n      description\n      amount\n    }\n  }\n}"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "mutation SummarizeTaxpayer($id: ID!) {\n  summarizeTaxpayer(id: $id) {\n    __typename\n    filingStatus\n    totalLiability\n    jurisdictionCount\n    riskBand\n  }\n}"): (typeof documents)["mutation SummarizeTaxpayer($id: ID!) {\n  summarizeTaxpayer(id: $id) {\n    __typename\n    filingStatus\n    totalLiability\n    jurisdictionCount\n    riskBand\n  }\n}"];

export function graphql(source: string) {
  return (documents as any)[source] ?? {};
}

export type DocumentType<TDocumentNode extends DocumentNode<any, any>> = TDocumentNode extends DocumentNode<  infer TType,  any>  ? TType  : never;