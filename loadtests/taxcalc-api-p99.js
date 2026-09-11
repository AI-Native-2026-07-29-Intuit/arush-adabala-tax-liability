// loadtests/taxcalc-api-p99.js
//
// W6 D5 Task 3 (RUNNABLE). Thresholds map EXACTLY to the W5 D5 SLO; the k6 exit code is the CI
// gate, so a breached threshold fails the check and blocks the merge before Argo CD ever pulls
// the change.
//
// ---------------------------------------------------------------------------------------------
// THE THREE SLO NUMBERS ARE COPIED, NOT CHOSEN
// ---------------------------------------------------------------------------------------------
// p99 <= 500ms, error rate < 0.005, cost/request < $0.003. They come from the W5 D5 SLO
// (slo/taxcalc-api.sloth.yaml) and the W6 D4 cost SLI. Nothing here may be loosened to make a
// run go green - a threshold tuned to fit the measurement is a threshold that measures nothing.
//
// ---------------------------------------------------------------------------------------------
// THE COST THRESHOLD READS A REAL HEADER, AND THAT IS THE WHOLE POINT
// ---------------------------------------------------------------------------------------------
// cost_per_request_usd reads the X-Cost-Usd response header that CostMiddleware sets. Two ways
// this threshold becomes decorative, both of which look like a pass:
//
//   1. No header at all. `parseFloat(undefined || '0')` is 0, every sample is 0, p(95) is 0, and
//      0 < 0.003 passes on every run forever. A cost gate that cannot fail is worse than none,
//      because it is reported as a control.
//   2. A header in scientific notation. A Haiku call costs ~$0.0002, and Double.toString renders
//      that as "2.0E-4" - which parseFloat does handle, but an awk pipeline or a dashboard axis
//      does not. W6 D4 formats the header with BigDecimal.toPlainString() precisely so every
//      consumer of it, including this one, reads the same number.
//
// The `costSamples` counter below exists to catch case 1: setup() asserts it is non-zero after
// the run, so a deployment that silently stopped emitting the header fails the gate instead of
// passing it perfectly.
//
// ---------------------------------------------------------------------------------------------
// WHY THE WORKLOAD MIX IS WEIGHTED THE WAY IT IS
// ---------------------------------------------------------------------------------------------
// Weights MUST sum to 1.0 (asserted at startup - see assertMixSumsToOne). The split is not
// aesthetic; each number is forced by something real:
//
//   0.55 write   POST /api/v1/taxpayers. Bracket resolution, a Postgres write and an outbox
//                insert - the heaviest non-LLM path, and the one that produces the
//                taxpayers.events records Task 1's KEDA trigger scales the worker on. Running
//                the load test therefore exercises both autoscalers at once.
//   0.40 read    GET /api/v1/taxpayers/{id}. Redis cache then Mongo read model. Cheap, cached,
//                and the path most real traffic takes.
//   0.05 llm     POST /v1/completions. The cost-bearing path, and the ONLY source of X-Cost-Usd.
//
// 0.05 is a hard ceiling, not a preference. RateLimitFilter buckets LLM routes at 10
// requests/minute PER JWT SUBJECT. Each VU sleeps ~0.5s, so it issues roughly 2 requests/second,
// i.e. ~120/minute; at weight 0.05 that is ~6 LLM requests/minute per subject, comfortably
// inside the limit with room for jitter. At the obvious-looking 0.2 it would be ~24/minute,
// every VU would start collecting 429s, and http_req_failed would blow through 0.005 - which
// reads as "the service fell over under load" when it is the cost control working exactly as
// designed. Note the per-VU rate is independent of the VU count, so this holds at 20 VUs and at
// 200.
//
// Each VU carries its OWN token (its own `sub`, minted by scripts/loadtest-token.sh), so the
// buckets are per synthetic caller. That models what the rate limiter is for rather than
// defeating it.
//
// ---------------------------------------------------------------------------------------------
// WHAT THIS RUN DOES AND DOES NOT MEASURE
// ---------------------------------------------------------------------------------------------
// The api runs in the `loadtest` profile, where SyntheticChatUpstream replaces the Anthropic
// call. So: the cost arithmetic, the price book, the header and the whole serving path are real;
// the token counts, and therefore the absolute dollar figure, are synthetic, and no measurement
// here says anything about Anthropic's latency. See SyntheticChatUpstream's javadoc for why a
// 200-VU six-minute run against the live provider is neither affordable nor meaningful.
import http from 'k6/http';
import { check, sleep, fail } from 'k6';
import { Trend, Counter } from 'k6/metrics';

const costPerReq = new Trend('cost_per_request_usd');
const costSamples = new Counter('cost_samples');

export const options = {
  stages: [
    { duration: '4m', target: 200 }, // ramp
    { duration: '6m', target: 200 }, // hold: the HPA and KEDA reach steady state
    { duration: '2m', target: 0 },   // down
  ],
  // A five-second spike would test cold-start, not steady state. The hold is what lets the HPA's
  // 0s scaleUp window actually add replicas and the metric settle - a shorter run measures the
  // autoscaler's reaction time and reports it as the service's latency.
  thresholds: {
    http_req_duration:    ['p(99)<500'],    // W5 D5 SLO
    http_req_failed:      ['rate<0.005'],   // W5 D5 error budget
    cost_per_request_usd: ['p(95)<0.003'],  // W6 D4 cost SLI
    cost_samples:         ['count>0'],      // the gate on the gate - see the header comment
    checks:               ['rate>0.99'],
  },
};

const BASE = __ENV.TARGET || 'http://taxcalc-api.taxcalc-dev.svc.cluster.local:8080';

// Think time between iterations. 0.5s is the SLO-gate default and models a caller that pauses;
// it is what makes 200 VUs a realistic ~300 req/s rather than a synthetic hammer.
//
// SLEEP=0 turns this into a SATURATION PROBE, and that is a different measurement with a
// different purpose. With no think time each VU holds exactly one request open at all times, so
// in-flight concurrency equals the VU count - which is the only way to drive
// taxcalc_inflight_requests to a chosen value on demand.
//
// This mode is how the HPA's averageValue: 6 was derived in the first place: ramp one replica
// until p99 crosses 500ms and read off the concurrency. It is also what proves the HPA scales
// at all, because at the gate's own settings it does NOT - measured at 200 VUs, this service
// serves ~300 req/s at 4.4ms, so per-pod concurrency peaks near 4 and stays under the target of
// 6. The HPA holding at minReplicas there is the autoscaler being correct, not idle.
const SLEEP_SECONDS = __ENV.SLEEP !== undefined ? parseFloat(__ENV.SLEEP) : 0.5;

// Tokens are minted out of band by scripts/loadtest-token.sh and never committed. Loaded once at
// init time (not per-VU): k6 shares init-context data across VUs, and re-reading the file per VU
// would multiply file IO by the VU count for no benefit.
const TOKENS = JSON.parse(open(__ENV.TOKENS || '../.loadtest/tokens.json'));

// Seeded by V2__seed.sql. These are read through the Mongo read model, which the worker
// populates from taxpayers.events - so the read slice also proves the Task 1 projection is
// actually running, not merely that the worker pod is up.
const SEEDED_IDS = [
  'tp-2026-0001', 'tp-2026-0002', 'tp-2026-0003', 'tp-2026-0004', 'tp-2026-0005',
];

const MIX = [
  { weight: 0.55, kind: 'write' },
  { weight: 0.40, kind: 'read' },
  { weight: 0.05, kind: 'llm' },
];

// The loadtest-author Skill silently RENORMALISES a mix that does not sum to 1.0, which turns a
// typo into a different test that still passes. Asserting instead means the typo is a startup
// failure naming the actual sum.
function assertMixSumsToOne() {
  const sum = MIX.reduce((acc, step) => acc + step.weight, 0);
  if (Math.abs(sum - 1.0) > 1e-9) {
    fail(`workload mix weights must sum to 1.0, got ${sum}`);
  }
}

export function setup() {
  assertMixSumsToOne();
  if (!Array.isArray(TOKENS) || TOKENS.length === 0) {
    fail('no tokens loaded - run scripts/loadtest-token.sh first');
  }
  return { tokenCount: TOKENS.length };
}

function pick() {
  const r = Math.random();
  let acc = 0;
  for (const step of MIX) {
    acc += step.weight;
    if (r <= acc) {
      return step.kind;
    }
  }
  return MIX[MIX.length - 1].kind;
}

function headersFor(vu) {
  // One token per VU, wrapping if there are fewer tokens than VUs. __VU is 1-based.
  const token = TOKENS[(vu - 1) % TOKENS.length];
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
    'X-Tenant': 'tenant-synth',
  };
}

// Record the cost header if there is one. Called ONLY for responses that should carry it: adding
// a 0 sample for every read and write would drag p(95) toward zero and mask a real cost
// regression behind the 95% of traffic that costs nothing.
function recordCost(res) {
  const raw = res.headers['X-Cost-Usd'];
  if (raw === undefined) {
    return;
  }
  costPerReq.add(parseFloat(raw));
  costSamples.add(1);
}

export default function () {
  const headers = headersFor(__VU);
  const kind = pick();

  if (kind === 'write') {
    // The body MUST match CreateTaxpayerRequest exactly: id, displayName, filingStatus,
    // taxableAmount. Its compact constructor requireNonNull's all four, and a missing field
    // surfaces as a 403 rather than a 400 - the deserialization failure happens during argument
    // resolution inside the @PreAuthorize proxy, so Spring reports it as an authorization
    // failure. Chasing that as a scope/role problem costs an afternoon; the token was fine.
    const id = SEEDED_IDS[__ITER % SEEDED_IDS.length];
    const res = http.post(`${BASE}/api/v1/taxpayers`,
      JSON.stringify({
        id: id,
        displayName: `taxcalc.example.internal/${id}`,
        filingStatus: 'SINGLE',
        taxableAmount: 9500.00 + (__ITER % 1000),
      }),
      { headers, tags: { kind: 'write' } });
    check(res, { 'write 2xx': (r) => r.status >= 200 && r.status < 300 });
  } else if (kind === 'read') {
    const id = SEEDED_IDS[__ITER % SEEDED_IDS.length];
    const res = http.get(`${BASE}/api/v1/taxpayers/${id}`, { headers, tags: { kind: 'read' } });
    check(res, { 'read 2xx': (r) => r.status >= 200 && r.status < 300 });
  } else {
    const res = http.post(`${BASE}/v1/completions`,
      JSON.stringify({
        model: 'claude-haiku-4-5',
        feature: 'explain-liability',
        prompt: 'Explain this taxpayer\'s 2026 federal liability in two sentences.',
      }),
      { headers, tags: { kind: 'llm' } });
    check(res, {
      'llm 2xx': (r) => r.status >= 200 && r.status < 300,
      // Asserted per-response as well as via the cost_samples threshold: this pinpoints WHICH
      // request lost the header, where the counter only says the run as a whole did.
      'llm carries X-Cost-Usd': (r) => r.headers['X-Cost-Usd'] !== undefined,
    });
    recordCost(res);
  }

  if (SLEEP_SECONDS > 0) {
    sleep(SLEEP_SECONDS);
  }
}
