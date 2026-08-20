// src/types/taxpayer.ts
//
// Mirror of the W2 D5 Mongo read-model shape. All money fields are STRINGS,
// not JS numbers - JS `number` is IEEE-754 binary64 and loses cents at
// scale. The page never does arithmetic on these strings; if it ever does,
// reach for `decimal.js` (the JS equivalent of BigDecimal).

export type TaxpayerLine = {
  readonly id: string;
  readonly amount: string; // BigDecimal-as-string, scale 2
};

export type Taxpayer = {
  readonly id: string;
  readonly filingStatus: string;
  readonly jurisdictionCount: number;
  readonly totalLiability: string; // BigDecimal-as-string, scale 2
  readonly lines: ReadonlyArray<TaxpayerLine>;
};
