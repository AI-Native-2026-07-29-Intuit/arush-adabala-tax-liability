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

2. ~~The `sub` condition's form.~~ **Resolved — measured, not assumed.**
   `.github/workflows/oidc-probe.yml` minted a real OIDC token from this
   repo on 2026-09-04 and decoded it. This organization does **not** issue
   the textbook `repo:<org>/<repo>:...` subject. It issues one carrying
   internal numeric org/repo ids:

   ```
   repo:AI-Native-2026-07-29-Intuit@309728071/arush-adabala-tax-liability@1317703842:pull_request
   ```

   Both files here now pin that real prefix. They shipped with the textbook
   form until this was measured — and had it stayed, every deploy would
   have failed with `Not authorized to perform
   sts:AssumeRoleWithWebIdentity`, an error naming neither `sub` nor the
   value AWS expected.

   **What is still inferred:** the *prefix* is observed; the three
   *suffixes* (`:environment:dev`, `:ref:refs/heads/main`,
   `:environment:prod`) follow GitHub's documented claim rules but have not
   been observed directly — a `pull_request` run cannot mint an
   environment-scoped token, and both Environments are pinned to `main`.
   Re-run `oidc-probe.yml` via `workflow_dispatch` after the first push to
   `main` to confirm them, and re-run the bootstrap if they differ. Re-run
   it too if the org or repo is ever renamed or recreated: those numeric
   ids are identity, not decoration.

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
