# Label discipline — taxcalc-api

The storage cost of an observability stack is decided almost entirely by label cardinality, and
almost always by accident. This file is the rule the team applies; the code enforcing it is
`CorrelationIdFilter`, `TaxpayerLookupService`, `logback-spring.xml` and the ServiceMonitor's
`metricRelabelings`.

## The rule in one line

**A label is a dimension you group by. An identifier is something you search for.** Identifiers go
in log fields and span attributes, never in Prometheus labels or Loki stream labels.

## The four permitted Loki labels

| Label  | Source                                              | Cardinality |
|--------|-----------------------------------------------------|-------------|
| `app`  | pod label `app.kubernetes.io/name` (Alloy relabel)  | 1 per service |
| `env`  | `customFields` in `logback-spring.xml`              | one per environment |
| `level`| `level` field of the JSON envelope (Alloy `stage.labels`) | 5 |
| `pod`  | pod name (Alloy relabel)                            | replica count |

Every other field on a log line stays *in the line body*, where Loki indexes it lazily at query
time instead of creating a stream for it.

## The three forbidden high-cardinality identifiers

These appear in the JSON log body and as span attributes. They must never become Loki labels or
Prometheus metric labels:

| Identifier      | Where it lives instead                                                        |
|-----------------|-------------------------------------------------------------------------------|
| `taxpayerId`    | log line body (`lookup attempted taxpayerId=…`); span attributes              |
| `correlationId` | log line body (MDC → JSON property); span attribute `correlation.id`          |
| user id / JWT `sub` | log line body (`get id=… subject=…`); never on a metric                   |

## Why this matters — the numbers

Loki builds one **stream** per unique label-set, and each stream carries its own index entry and
its own set of chunks. Prometheus builds one **time series** per unique label-set, and holds every
active series in memory.

- With the four labels above: `1 app × 1 env × 5 levels × 3 pods` = **15 streams**. Stable.
- Add `taxpayerId` as a label, at 50k taxpayers: **up to 750,000 streams** — the same log volume,
  spread across fifty thousand times more index entries and chunk files. Loki's ingesters hit
  their per-tenant stream limit and start rejecting writes; the logs you added the label to help
  you find are the ones that stop arriving.
- The Prometheus side of the same mistake: `taxcalc_liability_recomputed_total{taxpayer_id="…"}`
  at 50k taxpayers is 50k resident series **per pod**, each with its own samples for the full
  retention window, and they do not go away when a taxpayer stops being active — they age out.

The asymmetry is the point: a *label* costs storage for the entire retention window whether or not
anyone queries it, while a *field in the line body* costs nothing until a query filters on it.
`{app="taxcalc-api"} |= "txp_synth_001"` is a fast, cheap query — Loki narrows to 15 streams by
label, then greps. That is the intended shape.

## The AI-suggestion trap

The most common suggestion when adding a metric is "tag it with `userId` so you can group by
user." Reject it. If per-user analysis is genuinely needed, it belongs in traces (searchable by
attribute, sampled, retained briefly) or in the warehouse — not in a metrics store that is
optimised for a bounded number of series scraped every 15 seconds.
