/** Internal type. DO NOT USE DIRECTLY. */
type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
/** Internal type. DO NOT USE DIRECTLY. */
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
import { gql } from '@apollo/client';
import * as Apollo from '@apollo/client';
export type Maybe<T> = T | null;
export type InputMaybe<T> = Maybe<T>;
const defaultOptions = {} as const;
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string; }
  String: { input: string; output: string; }
  Boolean: { input: boolean; output: boolean; }
  Int: { input: number; output: number; }
  Float: { input: number; output: number; }
};

export type LineItem = {
  __typename?: 'LineItem';
  amount: Scalars['Float']['output'];
  description: Scalars['String']['output'];
  id: Scalars['ID']['output'];
};

export type Mutation = {
  __typename?: 'Mutation';
  summarizeTaxpayer: TaxpayerSummary;
};


export type MutationSummarizeTaxpayerArgs = {
  id: Scalars['ID']['input'];
};

export type Query = {
  __typename?: 'Query';
  latestTaxpayers: Array<Taxpayer>;
  taxpayer?: Maybe<Taxpayer>;
  taxpayersByTag: Array<Taxpayer>;
};


export type QueryLatestTaxpayersArgs = {
  limit?: InputMaybe<Scalars['Int']['input']>;
};


export type QueryTaxpayerArgs = {
  id: Scalars['ID']['input'];
};


export type QueryTaxpayersByTagArgs = {
  tag: Scalars['String']['input'];
};

export type Taxpayer = {
  __typename?: 'Taxpayer';
  id: Scalars['ID']['output'];
  lines: Array<LineItem>;
  tags: Array<Scalars['String']['output']>;
};

export type TaxpayerSummary = {
  __typename?: 'TaxpayerSummary';
  filingStatus: Scalars['String']['output'];
  jurisdictionCount: Scalars['Int']['output'];
  riskBand: Scalars['String']['output'];
  totalLiability: Scalars['Float']['output'];
};

export type LatestTaxpayersQueryVariables = Exact<{
  limit?: number | null | undefined;
}>;


export type LatestTaxpayersQuery = { latestTaxpayers: Array<{ id: string, tags: Array<string>, lines: Array<{ id: string, description: string, amount: number }> }> };

export type SummarizeTaxpayerMutationVariables = Exact<{
  id: string | number;
}>;


export type SummarizeTaxpayerMutation = { summarizeTaxpayer: { __typename: 'TaxpayerSummary', filingStatus: string, totalLiability: number, jurisdictionCount: number, riskBand: string } };


export const LatestTaxpayersDocument = gql`
    query LatestTaxpayers($limit: Int) {
  latestTaxpayers(limit: $limit) {
    id
    tags
    lines {
      id
      description
      amount
    }
  }
}
    `;

/**
 * __useLatestTaxpayersQuery__
 *
 * To run a query within a React component, call `useLatestTaxpayersQuery` and pass it any options that fit your needs.
 * When your component renders, `useLatestTaxpayersQuery` returns an object from Apollo Client that contains loading, error, and data properties
 * you can use to render your UI.
 *
 * @param baseOptions options that will be passed into the query, supported options are listed on: https://www.apollographql.com/docs/react/api/react-hooks/#options;
 *
 * @example
 * const { data, loading, error } = useLatestTaxpayersQuery({
 *   variables: {
 *      limit: // value for 'limit'
 *   },
 * });
 */
export function useLatestTaxpayersQuery(baseOptions?: Apollo.QueryHookOptions<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>) {
        const options = {...defaultOptions, ...baseOptions}
        return Apollo.useQuery<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>(LatestTaxpayersDocument, options);
      }
export function useLatestTaxpayersLazyQuery(baseOptions?: Apollo.LazyQueryHookOptions<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>) {
          const options = {...defaultOptions, ...baseOptions}
          return Apollo.useLazyQuery<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>(LatestTaxpayersDocument, options);
        }
// @ts-ignore
export function useLatestTaxpayersSuspenseQuery(baseOptions?: Apollo.SuspenseQueryHookOptions<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>): Apollo.UseSuspenseQueryResult<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>;
export function useLatestTaxpayersSuspenseQuery(baseOptions?: Apollo.SkipToken | Apollo.SuspenseQueryHookOptions<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>): Apollo.UseSuspenseQueryResult<LatestTaxpayersQuery | undefined, LatestTaxpayersQueryVariables>;
export function useLatestTaxpayersSuspenseQuery(baseOptions?: Apollo.SkipToken | Apollo.SuspenseQueryHookOptions<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>) {
          const options = baseOptions === Apollo.skipToken ? baseOptions : {...defaultOptions, ...baseOptions}
          return Apollo.useSuspenseQuery<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>(LatestTaxpayersDocument, options);
        }
export type LatestTaxpayersQueryHookResult = ReturnType<typeof useLatestTaxpayersQuery>;
export type LatestTaxpayersLazyQueryHookResult = ReturnType<typeof useLatestTaxpayersLazyQuery>;
export type LatestTaxpayersSuspenseQueryHookResult = ReturnType<typeof useLatestTaxpayersSuspenseQuery>;
export type LatestTaxpayersQueryResult = Apollo.QueryResult<LatestTaxpayersQuery, LatestTaxpayersQueryVariables>;
export const SummarizeTaxpayerDocument = gql`
    mutation SummarizeTaxpayer($id: ID!) {
  summarizeTaxpayer(id: $id) {
    __typename
    filingStatus
    totalLiability
    jurisdictionCount
    riskBand
  }
}
    `;
export type SummarizeTaxpayerMutationFn = Apollo.MutationFunction<SummarizeTaxpayerMutation, SummarizeTaxpayerMutationVariables>;

/**
 * __useSummarizeTaxpayerMutation__
 *
 * To run a mutation, you first call `useSummarizeTaxpayerMutation` within a React component and pass it any options that fit your needs.
 * When your component renders, `useSummarizeTaxpayerMutation` returns a tuple that includes:
 * - A mutate function that you can call at any time to execute the mutation
 * - An object with fields that represent the current status of the mutation's execution
 *
 * @param baseOptions options that will be passed into the mutation, supported options are listed on: https://www.apollographql.com/docs/react/api/react-hooks/#options-2;
 *
 * @example
 * const [summarizeTaxpayerMutation, { data, loading, error }] = useSummarizeTaxpayerMutation({
 *   variables: {
 *      id: // value for 'id'
 *   },
 * });
 */
export function useSummarizeTaxpayerMutation(baseOptions?: Apollo.MutationHookOptions<SummarizeTaxpayerMutation, SummarizeTaxpayerMutationVariables>) {
        const options = {...defaultOptions, ...baseOptions}
        return Apollo.useMutation<SummarizeTaxpayerMutation, SummarizeTaxpayerMutationVariables>(SummarizeTaxpayerDocument, options);
      }
export type SummarizeTaxpayerMutationHookResult = ReturnType<typeof useSummarizeTaxpayerMutation>;
export type SummarizeTaxpayerMutationResult = Apollo.MutationResult<SummarizeTaxpayerMutation>;
export type SummarizeTaxpayerMutationOptions = Apollo.BaseMutationOptions<SummarizeTaxpayerMutation, SummarizeTaxpayerMutationVariables>;