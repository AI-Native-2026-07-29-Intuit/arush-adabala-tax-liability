#!/usr/bin/env bash
# scripts/verify-pgvector.sh - run the two psql-level "Done when" checks for W6 D4 Task 3 against
# a real Postgres, from a real psql session:
#
#   1. SELECT extname FROM pg_extension WHERE extname = 'vector'   -> exactly one row
#   2. EXPLAIN ANALYZE ... ORDER BY embedding <=> $1 LIMIT 5       -> Index Scan using
#                                                                     taxpayer_embeddings_hnsw
#
# WHY THIS EXISTS ALONGSIDE TaxpayerEmbeddingsRepoTest
#
# That test asserts the same two facts, and it is the one CI runs - but it asserts the second one
# with `EXPLAIN` (not ANALYZE) and with `enable_seqscan`/`enable_sort = off`. Those settings are
# there on purpose: they strip the planner's cost arithmetic out of the picture so the test
# isolates one question, "can this index serve a cosine query at all", which is the question an
# operator-class mismatch silently answers no to. The price is that the test does NOT show the
# planner CHOOSING the index on its own.
#
# This script pays the other half. It seeds enough rows for the cost model to have a real opinion,
# leaves every planner setting alone, and runs EXPLAIN ANALYZE - so what it reports is the plan a
# production query would actually get, execution timings included. It is not in CI because the
# seed is slow (tens of seconds) and the row count needed to make the planner's choice meaningful
# is a moving target across Postgres versions; that combination belongs in a script you run, not
# in a gate that blocks a merge.
#
# Usage:
#   scripts/verify-pgvector.sh            # 5000 rows (enough for the planner to prefer the index)
#   ROWS=50000 scripts/verify-pgvector.sh # more rows, slower seed
#   KEEP=1 scripts/verify-pgvector.sh     # leave the container up to poke at by hand
#
# Requires docker. Starts its own throwaway container and removes it on exit, so it touches
# nothing the application or the test suite uses.

set -euo pipefail

IMAGE="${IMAGE:-pgvector/pgvector:pg16}"
ROWS="${ROWS:-5000}"
KEEP="${KEEP:-0}"
CONTAINER="taxcalc-pgvector-verify-$$"
PASSWORD="verify"
PSQL=(docker exec -e PGPASSWORD="$PASSWORD" -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d postgres)

# Repo root, so this runs the committed migrations from wherever it is invoked.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS="$ROOT/src/main/resources/db/migration"

cleanup() {
    if [[ "$KEEP" == "1" ]]; then
        echo "KEEP=1: container $CONTAINER left running. Remove it with: docker rm -f $CONTAINER"
        return
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

echo "==> starting $IMAGE as $CONTAINER"
docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD="$PASSWORD" "$IMAGE" >/dev/null

echo "==> waiting for Postgres"
for _ in $(seq 1 60); do
    if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1 || fail "Postgres never became ready"

# The committed migrations, in version order - not a hand-written CREATE TABLE. A bespoke schema
# here would verify a schema nothing deploys, which is the failure mode this whole script is
# meant to rule out.
echo "==> applying migrations from ${MIGRATIONS#"$ROOT"/}"
for migration in "$MIGRATIONS"/V*.sql; do
    echo "    $(basename "$migration")"
    "${PSQL[@]}" -q < "$migration"
done

# ---------------------------------------------------------------- check 1: the extension

echo "==> check 1: the vector extension is installed"
EXTENSION_ROWS="$("${PSQL[@]}" -tAc "SELECT extname FROM pg_extension WHERE extname = 'vector'")"
[[ "$EXTENSION_ROWS" == "vector" ]] || fail "expected exactly one row 'vector', got: '${EXTENSION_ROWS}'"
echo "    extname = vector"

# ---------------------------------------------------------------- seed

# Correlated on g.i (via the always-true predicate) so random() is re-evaluated per row. Without
# that correlation Postgres is free to evaluate the sub-select once as an InitPlan and insert
# ROWS copies of ONE vector - which would still be ROWS rows, and would still produce an index
# scan, while testing nothing about nearest-neighbour behaviour.
echo "==> seeding $ROWS random 1024-dimension vectors (this is the slow part)"
time "${PSQL[@]}" -q -c "
    INSERT INTO taxcalc.taxpayer_embeddings (id, tenant_id, embedding)
    SELECT gen_random_uuid(),
           'acme',
           (SELECT array_agg(random())
              FROM generate_series(1, 1024) AS d(k)
             WHERE d.k + g.i > 0)::vector
      FROM generate_series(1, $ROWS) AS g(i);
"
"${PSQL[@]}" -q -c "ANALYZE taxcalc.taxpayer_embeddings;"
echo "    rows: $("${PSQL[@]}" -tAc 'SELECT count(*) FROM taxcalc.taxpayer_embeddings')"

# ---------------------------------------------------------------- check 2: the plan

# The query vector is fetched FIRST and inlined as a literal, rather than named as a sub-select
# inside the EXPLAIN. A sub-select would put its own `Seq Scan ... LIMIT 1` in the plan - harmless,
# since it reads one row and has nothing to do with the ranking, but it makes the plan impossible
# to check mechanically for a sequential scan. Inlining the literal also matches the shape the
# application sends, where the vector is a bound parameter (`<=> ?::vector`) and never a subquery.
QUERY_VECTOR="$("${PSQL[@]}" -tAc "SELECT embedding FROM taxcalc.taxpayer_embeddings LIMIT 1")"
[[ -n "$QUERY_VECTOR" ]] || fail "could not read a seeded vector to query with"

# NO planner settings touched. This is the plan a production query gets.
echo "==> check 2: EXPLAIN ANALYZE picks the HNSW index, with default planner settings"
PLAN="$("${PSQL[@]}" -tA -c "
    EXPLAIN ANALYZE
    SELECT id
      FROM taxcalc.taxpayer_embeddings
     WHERE tenant_id = 'acme'
     ORDER BY embedding <=> '$QUERY_VECTOR'::vector
     LIMIT 5;
")"

# The query vector appears verbatim in the plan's Order By line - 1024 floats, ~20KB of noise
# that buries the one line anybody reads. Abbreviated for display only; every check below runs
# against the full plan.
echo "$PLAN" | sed -E "s/'\[[-0-9.,e]{200,}\]'/'[<1024 dims>]'/g" | sed 's/^/    /'

if ! grep -q "taxpayer_embeddings_hnsw" <<<"$PLAN"; then
    fail "the plan does not use taxpayer_embeddings_hnsw. With ROWS=$ROWS the planner may simply
      have costed a sort below an index scan - retry with a larger ROWS before concluding the
      index is wrong. An index the planner never picks is usually a cost question; an index it
      CANNOT pick is an operator-class mismatch, which TaxpayerEmbeddingsRepoTest tests directly."
fi
if grep -qE "Seq Scan on taxpayer_embeddings" <<<"$PLAN"; then
    fail "the plan contains a sequential scan over taxpayer_embeddings"
fi

echo
echo "PASS: extension installed, and ORDER BY embedding <=> ... LIMIT 5 is served by"
echo "      Index Scan using taxpayer_embeddings_hnsw at $ROWS rows, planner settings untouched."
