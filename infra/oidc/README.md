# infra/oidc/ — taxcalc-api's two GitHub Actions deploy roles

`trust-policy-build.json` and `trust-policy-prod.json` are the committed,
reproducible shape of the two IAM trust policies `.github/workflows/
_build-and-push.yml` and `.github/workflows/deploy-prod.yml` assume via
OIDC — no long-lived `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` anywhere in
this repo. Apply them with `scripts/docker-oidc-bootstrap.sh`, not by hand:
that script substitutes the two placeholders below with real, discovered
values before calling `aws iam create-role`/`update-assume-role-policy` —
neither file is valid to apply as committed.

## Two placeholders that MUST be resolved before applying

1. **`123456789012` (the account id in `Principal.Federated`).** A stand-in,
   the same convention `scripts/oidc-bootstrap.sh` and `docker/SECURITY.md`
   use elsewhere in this repo. The bootstrap script discovers the real
   value via `aws sts get-caller-identity` and substitutes it — never
   hand-edit this file with a guessed account id.

2. **The `sub` condition's literal `repo:AI-Native-2026-07-29-Intuit/...`
   form.** This is the textbook shape, and `scripts/oidc-bootstrap.sh`
   already found — for the W5 D4 serverless deploy role, against this
   exact organization — that GitHub's real token issues a **non-standard**
   subject claim embedding internal numeric ids:

   ```
   repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842:pull_request
   ```

   A trust policy pinned to the textbook form almost certainly will not
   match here, and the failure is opaque
   (`Not authorized to perform sts:AssumeRoleWithWebIdentity`, no mention
   of `sub` anywhere in the error). **Do not trust the literal value in
   these two files.** Before running the bootstrap script for real,
   confirm the actual subject the same way `oidc-bootstrap.sh`'s own header
   comment documents: add a temporary `- name: Inspect OIDC token claims`
   step to the workflow that decodes its own `ACTIONS_ID_TOKEN_REQUEST_TOKEN`
   JWT and prints the `sub` claim, run it once from a real push-to-main
   (not a `pull_request` event — the two roles here only ever assume on
   `push`/`environment`, and the claim shape differs by event type), then
   pass the confirmed value as `SUBJECT_DEV`/`SUBJECT_MAIN`/`SUBJECT_PROD`
   to the bootstrap script rather than letting it use these files' literal
   defaults.

## Why two roles, not one

- **`taxcalc-api-build-push`** (`trust-policy-build.json`) — assumed by
  `_build-and-push.yml`'s `build-scan-push` job, which runs under the `dev`
  GitHub Environment on every push to `main`. Its `sub` condition
  (`StringLike`, a list) pins to **both** `environment:dev` (the
  Environment gate the job actually runs under) **and**
  `ref:refs/heads/main` (belt-and-suspenders: even if the job's own
  `environment:` line were ever removed, the branch condition alone still
  stops a PR-triggered run, which never has `id-token: write`-scoped OIDC
  access to this role in the first place, from assuming it). Permissions:
  `ecr:GetAuthorizationToken` (must be `Resource: "*"` — ECR does not
  support resource-scoping this action) plus
  `ecr:BatchCheckLayerAvailability`/`CompleteLayerUpload`/
  `InitiateLayerUpload`/`PutImage`/`UploadLayerPart`, scoped to the one
  `uptimecrew/taxcalc-api` repository ARN.

- **`taxcalc-api-prod-deploy`** (`trust-policy-prod.json`) — assumed by
  `deploy-prod.yml`, gated behind the `prod` GitHub Environment's required
  reviewers. Its `sub` condition pins to `environment:prod` **only** —
  deliberately **not** `ref:refs/heads/main` or any branch condition at
  all. `workflow_dispatch` can be triggered from any branch/tag a caller
  has push access to, so the *only* thing standing between "just anyone
  with write access" and this role is the Environment's required-reviewer
  gate, which is what actually decides whether the run starts at all — a
  branch condition here would be theatre, since GitHub evaluates the
  Environment gate before the job (and its OIDC token request) ever runs.
  Permissions today: `ecr:DescribeImages` only, scoped to the same
  repository ARN — enough for `deploy-prod.yml`'s "confirm the image
  exists" step. W6 D3 widens this when the real `kubectl rollout`/`sam
  deploy` step replaces today's placeholder (see that workflow's own
  comment).

## Applying

```bash
AWS_PROFILE=dev ./scripts/docker-oidc-bootstrap.sh
```

Prints the two role ARNs and the exact `gh variable set` commands to wire
them into this repo (`AWS_ACCOUNT_ID`, `AWS_REGION`) — variables, not
secrets, since an account id and a region are not confidential and hiding
them only makes a failed `role-to-assume` harder to read, the same
rationale `scripts/oidc-bootstrap.sh` gives for `AWS_DEPLOY_ROLE_ARN`.
