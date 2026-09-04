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
3. The PR's `Build + test (taxcalc-api)` status check **is required on
   `main`**. Branch protection was unavailable while this repo was private on
   a free-plan org (both the classic and ruleset APIs returned `403:
   "Upgrade to GitHub Pro or make this repository public"`); the repo was
   made public on 2026-09-04, which lifted the gate, and the rule was created
   immediately after. The rule selects the check by its **display name**
   `Build + test (taxcalc-api)`, not the job id `build-test` - `ci.yml`'s
   `name:` override is what publishes it, and a rule naming `build-test`
   would silently match nothing. Current settings: `strict: false` (a branch
   need not be up to date before merging) and `enforce_admins: false` (repo
   admins can still push directly). Tighten either if you want the rule to
   bind admins too.
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
   - pushes `<git-sha>` (immutable, what `deploy-prod.yml` and any future
     manifest reference) **and** `main` (mutable dev-convenience tag - never
     `latest`, per docker/SECURITY.md's existing rule) to **two registries**:

     | registry | when | why |
     |---|---|---|
     | `ghcr.io/<owner>/taxcalc-api` | always | Needs nothing but this repo's own `GITHUB_TOKEN`, so `main` publishes a real, pullable image today - which is what W6 D2's Argo CD and W6 D3's `kubectl` rollout will need to pull. |
     | ECR `uptimecrew/taxcalc-api` | only when `vars.AWS_ACCOUNT_ID` is set | The deliverable's target, unchanged and unfaked. Inert until the AWS account exists; every AWS step is `if:`-gated and the job prints a warning naming the missing variable rather than skipping silently. |

     Setting `AWS_ACCOUNT_ID` activates the ECR half with no workflow change.
3. The job runs under the `dev` GitHub Environment, which both gates its
   secrets/vars and is what the `taxcalc-api-build-push` trust policy's
   `sub` condition pins to - this **is** the "dev auto-deploys on merge to
   main" step; there is no separate deploy action beyond the push itself
   until W6 D2/D3 land Argo CD / `kubectl rollout` against it.

## Before the first push to main — one-time AWS bootstrap

`call-build-and-push` has never run: `ci.yml` has only ever fired on
`pull_request` so far, and the AWS side it needs does not exist yet. Three
things are required, in order, and none of them can be done from inside this
repo alone - they need credentials for the target AWS account:

```bash
# 1. Create the OIDC provider, both IAM roles, and the ECR repository.
#    Read infra/oidc/README.md FIRST - the sub-claim caveat there is real,
#    and a policy pinned to the wrong subject fails opaquely.
AWS_PROFILE=<admin-profile> ./scripts/docker-oidc-bootstrap.sh

# 2. Wire the account id in (the script prints this exact command with the
#    real value substituted). AWS_REGION is already set on this repo.
gh variable set AWS_ACCOUNT_ID --body '<account-id>'

# 3. Merge to main. call-build-and-push then runs for the first time.
```

Until step 2 is done, the job stops at its own preflight step with an
explicit `::error::` naming the missing variable, rather than failing six
steps later on a malformed `arn:aws:iam:::role/...` that says nothing about
which value is absent.

## To deploy to prod

1. In the GitHub UI, **Actions** → `deploy-prod` → **Run workflow**.
2. Paste the image SHA confirmed pushed to ECR (the same 40-char SHA
   `call-build-and-push` tagged in step 2 above).
3. The run is pinned to the `prod` Environment, and **both** of its gates are
   live: the deployment-branch policy (`main` only), and a required-reviewer
   rule naming `ArushAdabala` and `yusufumautiauptimecrew`. The run parks on
   "Waiting for review" until one of them approves it in the Actions tab.
   The reviewer rule was impossible while this repo was private on a free
   plan (`PUT .../environments/prod` returned `422 "...billing plan supports
   the required reviewers protection rule"`); making the repo public on
   2026-09-04 lifted that. `prevent_self_review` is **off**, so whoever
   dispatches a promotion may approve it - turn it on if you want a genuine
   second pair of eyes, but note that a solo on-call then cannot ship.
4. `concurrency: deploy-prod-taxcalc-api` with `cancel-in-progress: false`
   means two promotions dispatched back-to-back **queue** rather than race.
   Measured, not assumed: runs `33849006639` and `33849009583` executed
   07:30:10-16 and 07:30:26-32 - no overlap, neither cancelled.
5. The workflow assumes `taxcalc-api-prod-deploy` (a **narrower** role than
   the build role - `ecr:DescribeImages` only today, see `infra/oidc/
   README.md`) and confirms the image exists in ECR. Both AWS steps are
   `if: vars.AWS_ACCOUNT_ID != ''`-gated, the same pattern
   `_build-and-push.yml` already uses: with the variable unset the run still
   goes green through the environment and concurrency gates and prints an
   explicit `::warning::` naming what is skipped, instead of failing on a
   malformed `arn:aws:iam:::role/...` that says nothing about which value is
   absent. W6 D3 lands the real `kubectl rollout`/Argo CD promotion step;
   today's placeholder step just echoes what it would apply.

## Why every action is SHA-pinned

`@v4` resolves to whatever bytes the maintainer most recently tagged. If
that tag is compromised, every workflow re-runs with the malicious code on
its next trigger - the October 2024 `tj-actions/changed-files` supply-chain
attack is the canonical reason this is non-optional. Pinning to a 40-char
commit SHA freezes the bytes; `.github/dependabot.yml` opens a grouped
weekly PR when a new version is available, and the comment next to each SHA
(`# v4.5.0`) tells reviewers which version a SHA corresponds to.

Every action in `.github/` is pinned - all eleven workflows and the composite
action, not just the three this deliverable added. Both the audit greps are
zero:

```bash
grep -RIn '@v[0-9]\+$' .github/                       # -> 0 (the task's own check)

# Stricter: every `uses:` that is not a local path must be a 40-hex sha with a
# version comment. Catches what the above misses - `@v3.1.0`, `@v0.36.0`, and a
# bare sha with no comment are all invisible to a `@v<n>$` anchor.
grep -RInE '^\s*(- )?uses: ' .github/ | grep -vE 'uses: \./' \
  | grep -vE '@[0-9a-f]{40} # v'                      # -> 0
```

**One sha per action, repo-wide.** Where an action appeared both pinned (in
this deliverable's files) and tagged (in an older workflow), everything was
unified onto a single sha rather than leaving `actions/checkout` at `v4.2.2`
in `ci.yml` and `v4.4.0` in `docker.yml` - two shas for one action is exactly
the ambiguity the `# v<n>` comment exists to remove, and it would have made
Dependabot open two PRs for one bump. The older workflows were resolving `@v4`
to the latest v4 on every run anyway, so pinning them *to* that latest sha
changes no bytes they were already executing; the W6 D1 files moved forward a
few minor versions to meet them.

**Resolving a sha correctly.** The obvious one-liner has two traps, and
`scripts/` does not hide either:

```bash
gh api /repos/<owner>/<repo>/git/matching-refs/tags/v4 --jq '.[-1].object.sha'
```

1. `matching-refs` sorts refs as **strings**, so `v4.9.0` sorts *after*
   `v4.10.0` and `.[-1]` silently returns the older tag. Sort with `sort -V`.
2. For an **annotated** tag, `.object.sha` is the tag object, not the commit.
   A workflow pinned to a tag-object sha fails at runtime. Deref it through
   `/git/tags/{sha}` and take `.object.sha`.

`aws-actions/setup-sam` is pinned `# v2` rather than `# v2.x.y` because that
repo publishes only moving major tags (`v0`..`v3`); there is no patch tag to
name. It is still pinned to the commit `v2` pointed at, so the pin is real.

## Repository protection - both gates are live

Neither of these is in version control; both are repo settings, recorded here
because the UI is the only place they exist. Both were **impossible until
2026-09-04**, when the repo was made public.

**`main` branch protection.** Requires the `Build + test (taxcalc-api)` status
check. It selects the check by **display name** - `ci.yml`'s `name:` override
is what publishes it, so a rule naming the job id `build-test` matches nothing
at all and silently protects nothing.

```
required_status_checks.contexts = ["Build + test (taxcalc-api)"]
strict          = false   # a branch need not be up to date before merging
enforce_admins  = false   # repo admins may still push directly to main
```

**`prod` Environment.** Deployment branches restricted to `main`, plus a
required-reviewer rule naming `ArushAdabala` and `yusufumautiauptimecrew`. A
`deploy-prod` dispatch now parks on "Waiting for review" until one of them
approves. `prevent_self_review` is **off**, so the dispatcher may self-approve
- turn it on for a genuine second pair of eyes, at the cost of making prod
undeployable by a lone on-call. `wait_timer` is 0, so GitHub stores no timer
rule at all (that is the correct representation of "wait timer = 0", not a
missing setting).

**Why they were absent before.** Both are plan-gated for *private* repos.
While this repo was private on a `plan: free` org, `PUT .../environments/prod`
returned `422 "Please ensure the billing plan supports the required reviewers
protection rule"` (identically for `wait_timer`), and both
`GET .../branches/main/protection` and `GET .../rulesets` returned `403:
"Upgrade to GitHub Pro or make this repository public to enable this
feature."` No permission grant could fix either - the features did not exist
on that plan, for anyone, via API or UI. An earlier revision of this file
blamed a safety classifier for the missing reviewer rule; that was wrong, and
the 422 above is what actually refused it.

## What is NOT in this repo

- **A publicly pullable image. The GHCR package is `private`, and that is
  blocked rather than chosen.** Package visibility does NOT inherit from
  repository visibility - the repo went public on 2026-09-04 and the package
  did not follow. The decision was made to publish it (a public package needs
  no pull credentials at all, whereas a private one needs a long-lived GitHub
  PAT living in the cluster as an `imagePullSecret` - which sits badly beside
  the no-long-lived-credentials premise the OIDC work just established), but
  it could not be carried out: **the org administrators have disabled changing
  package visibility.** The REST API cannot do it either - GET, DELETE and
  restore are the only package operations GitHub exposes, so `PATCH ...
  -f visibility=public` returns 404 and the web UI is the only route, which is
  the route that is disabled.

  **Consequence for W6 D2/D3:** the Argo CD / `kubectl` rollout will not be
  able to pull `ghcr.io/ai-native-2026-07-29-intuit/taxcalc-api` without an
  `imagePullSecret`. Budget for creating one, or get the org policy changed
  first. Discovering this at deploy time is the failure mode this note exists
  to prevent.
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
