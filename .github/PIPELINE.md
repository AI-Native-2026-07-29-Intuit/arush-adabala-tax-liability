# .github/PIPELINE.md

What the taxcalc-api CI/CD pipeline does, where the artefacts go, and how to
deploy. Written at `.github/PIPELINE.md`, not `taxcalc-api/.github/
PIPELINE.md`: this repo has no `taxcalc-api/` subdirectory - `build.gradle`,
`src/`, and now `.github/` all live at the repo root (see docker/
SECURITY.md's "Known deviations" table, first bullet).

## On every PR

1. `build-test` (`.github/workflows/ci.yml`) runs `./gradlew build` -
   compile, the full JUnit 5 suite (unit + the Testcontainers-backed
   integration tests across ~35 `@SpringBootTest`/`@DataJpaTest`/
   `@DataMongoTest` classes), and the JaCoCo 70% branch-coverage gate - on
   `blacksmith-2vcpu-ubuntu-2204` via the `setup-build` composite action.
2. Test + JaCoCo HTML/XML reports upload only on failure (saves artefact
   storage).
3. The PR's `Build + test (taxcalc-api)` status check is required by branch
   protection on `main`.
4. Separately, unchanged by this deliverable: `docker.yml` (W5 D1) still
   runs `hadolint` → `build-scan-smoke` (build the image, Trivy-scan it,
   boot it against real Postgres/Mongo service containers, hit `/actuator/
   health/readiness`) on the same PR - see "Relationship to docker.yml"
   below for why these are two separate gates rather than one merged job.

## On merge to main

1. `build-test` re-runs against the merged SHA.
2. `call-build-and-push` (needs `build-test`, `if: push to main` only)
   invokes the reusable workflow `_build-and-push.yml`, which:
   - assumes the OIDC role `taxcalc-api-build-push` (no long-lived AWS keys
     anywhere in this repo - see `infra/oidc/README.md`),
   - logs in to ECR,
   - builds the image with Buildx + the GitHub Actions cache,
   - runs Trivy with `HIGH,CRITICAL` failing the job (`.trivyignore`'s
     existing dated waiver from `docker/SECURITY.md` still applies - a
     fresh, un-waived finding still fails; the 23 already-documented ones
     don't re-litigate here),
   - pushes the image as `uptimecrew/taxcalc-api:<git-sha>` (immutable,
     what `deploy-prod.yml` and any future manifest reference) **and**
     `uptimecrew/taxcalc-api:main` (mutable dev-convenience tag - never
     `latest`, per docker/SECURITY.md's existing rule).
3. The job runs under the `dev` GitHub Environment, which both gates its
   secrets/vars and is what the `taxcalc-api-build-push` trust policy's
   `sub` condition pins to - this **is** the "dev auto-deploys on merge to
   main" step; there is no separate deploy action beyond the push itself
   until W6 D2/D3 land Argo CD / `kubectl rollout` against it.

## To deploy to prod

1. In the GitHub UI, **Actions** → `deploy-prod` → **Run workflow**.
2. Paste the image SHA confirmed pushed to ECR (the same 40-char SHA
   `call-build-and-push` tagged in step 2 above).
3. A required reviewer on the `prod` GitHub Environment approves the run
   before it starts - see "What is NOT in this repo" below.
4. The workflow assumes `taxcalc-api-prod-deploy` (a **narrower** role than
   the build role - `ecr:DescribeImages` only today, see `infra/oidc/
   README.md`) and confirms the image exists in ECR. W6 D3 lands the real
   `kubectl rollout`/Argo CD promotion step; today's placeholder step just
   echoes what it would apply.

## Why every action is SHA-pinned

`@v4` resolves to whatever bytes the maintainer most recently tagged. If
that tag is compromised, every workflow re-runs with the malicious code on
its next trigger - the October 2024 `tj-actions/changed-files` supply-chain
attack is the canonical reason this is non-optional. Pinning to a 40-char
commit SHA freezes the bytes; `.github/dependabot.yml` opens a grouped
weekly PR when a new version is available, and the comment next to each SHA
(`# v4.5.0`) tells reviewers which version a SHA corresponds to.

```bash
grep -RIn '@v[0-9]\+\.\?[0-9]*\.\?[0-9]*$' .github/workflows/ci.yml \
  .github/workflows/_build-and-push.yml .github/workflows/deploy-prod.yml \
  .github/actions/setup-build/action.yml
# -> 0 matches: every action this deliverable added is SHA-pinned.
```

**Known, deliberate gap - scope, not an oversight.** That same grep against
the *whole* `.github/` tree is not zero: every one of `docker.yml`,
`compose-ci.yml`, `k8s-ci.yml`, `observability.yml`, `serverless.yml`, and
`web-ci.yml` (all landed on earlier days, already graded, already green on
`main`) still pins at least one action by tag (`docker/build-push-action@
v6`, `hadolint/hadolint-action@v3.1.0`, `aquasecurity/trivy-action@v0.36.0`,
`actions/checkout@v4`, ...). Rewriting six unrelated workflow files' action
pins is a real, non-trivial change (wrong SHA = a silently broken build on
a day this deliverable doesn't own) with a blast radius well outside "wire
the CI/CD pipeline for W6 D1" - left as a follow-up rather than done
silently as a drive-by here, the same way every other day in this README
calls out what it deliberately left alone instead of fixing everything it
touched.

## What is NOT in this repo

- **The `prod` Environment's required-reviewer rule.** `dev` and `prod`
  GitHub Environments both exist (created via the API for this PR, deploy
  branch policy pinned to `main` on both), but *adding a required reviewer*
  to `prod` is a permission-granting action this session's safety
  classifier declined to take unattended - the same category of action the
  lesson itself calls out as "the UI configuration is not in version
  control" (screenshot it for the PR, don't try to script it). Add it
  manually: **Settings → Environments → prod → Required reviewers**, add
  yourself and your ES.
- **Branch protection on `main` requiring the `build-test` status check.**
  Same reasoning - a repo-admin mutation, not a file this PR ships. Add via
  **Settings → Branches → Branch protection rules**, add a rule for `main`
  requiring the `Build + test (taxcalc-api)` status check, once this PR's
  own `build-test` run has gone green at least once (a required check that
  has never run yet still blocks merges the same way, but going green once
  first is the honest way to confirm the check name matches exactly).
- **`kubectl apply` against EKS** - W6 D3.
- **`sam deploy` for the LLM cost-monitoring Lambda** - W6 D4.
- **Argo CD GitOps** - W6 D2 (replaces `deploy-prod.yml`'s eventual
  `kubectl apply` push pattern with a manifest-repo commit instead).
- **SLSA provenance / cosign image signing** - a later security day.

## AI-tool review note

The `github-actions-author` Claude Skill this deliverable's lesson names as
the Task 4 scaffold-then-audit step was not available in this session's
tool listing, so the workflow YAML in this PR was hand-authored directly
against the cohort checklist (the same six artefacts the lesson's own
appendix reproduces as canonical shape) rather than scaffolded and then
corrected. Two of the checklist's own named "common quirks" - `@v4` without
a SHA, and a redundant `actions/cache@v4` step alongside `setup-java`'s
built-in `cache: gradle` - were checked for directly (`grep -RIn '@v[0-9]'`
and `grep -RIn 'actions/cache'` across everything this PR added, both zero
matches) in place of the Skill's own audit pass.
