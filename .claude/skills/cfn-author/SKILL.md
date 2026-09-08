---
name: cfn-author
description: Scaffold a complete raw-YAML CloudFormation substrate for a service - a bootstrap stack (artefact bucket + OIDC deploy role), a multi-AZ VPC stack with Conditions-gated NAT, an application stack consuming the network via !ImportValue, a hardened S3 artefact bucket, and the cfn-lint/cfn-nag CI gate. Use when asked to author, scaffold, or independently review a CloudFormation stack set for a service.
---

# cfn-author

## Provenance — read this before trusting the output

This skill was **authored locally**, not distributed by the course. The W6 D3
deliverable names `/cfn-author` as a provided tool; it was absent from the
session's skill listing, exactly as `argocd-author` was for W6 D2 and
`github-actions-author` for W6 D1. It is written here from standard
CloudFormation conventions and the cohort reference layout so that the audit
pass the deliverable asks for has something to audit.

That has a consequence for how much weight its disagreements carry. A
generator written by the same person who wrote the artefacts under review is
a weaker check than an independent one — it will tend to agree.
**Under-disagreement is the expected failure mode, so a clean diff is not
evidence the artefacts are correct.** Any audit citing this skill must say so,
and must say whether the pass was actually cold.

## Arguments

```
/cfn-author <service-name> [--region <r>] [--env <e>] [--vpc-cidr <cidr>] [--out <dir>]
```

Arriving as one raw string; parse it yourself.

| Argument | Values | Default |
|---|---|---|
| `<service-name>` | positional, required | — |
| `--region` | any AWS region | `us-east-1` |
| `--env` | `dev`, `staging`, `prod` | `dev` |
| `--vpc-cidr` | a /16 | `10.41.0.0/16` |
| `--out` | directory | `.cfn-author-out/` |

## Hard rule: this is a cold pass

**Do not read the repository's existing `cfn/` directory, its
`cfn-validate.yml`, or any INFRA.md before generating.** The entire value of
this skill is that its output was produced without sight of the artefacts it
will be compared against; reading them first collapses the comparison into a
restatement.

Derive everything from the arguments and the conventions below. For facts you
cannot invent — container port, engine version, image repository — read the
application's own `Dockerfile` and `manifests/`, nothing else. If a fact is
genuinely unavailable, emit a clearly-marked placeholder rather than guessing
silently.

Write to the `--out` directory or a `scratch/cfn-author` branch. **Never
overwrite tracked artefacts.**

## Artefacts to generate

Raw YAML only. CDK and SAM are out of scope — the point of this exercise is
that the engineer reads the resource-level diff a ChangeSet produces, and a
synthesiser puts a layer between them and it.

### 1. `cfn/<service>-bootstrap-<env>.yaml`

The first stack. An S3 artefact bucket for `aws cloudformation package`, plus
the IAM role GitHub Actions assumes via OIDC to deploy every later stack.

- **Parameters** — `EnvName` (AllowedValues dev/staging/prod), `RetentionDays`
  (Number, MinValue 7, MaxValue 3650), `GitHubOrg`, `GitHubRepo`.
- **Bucket** — PublicAccessBlock on all four toggles; `BucketEncryption` with
  `aws:kms` + `alias/aws/s3`; versioning on; lifecycle rule expiring
  noncurrent versions after `!Ref RetentionDays`; a bucket policy with an
  explicit `Deny` on `aws:SecureTransport: false`.
- **Role** — `sts:AssumeRoleWithWebIdentity` against
  `arn:aws:iam::${AWS::AccountId}:oidc-provider/token.actions.githubusercontent.com`.
  Inline policy enumerating the CloudFormation ChangeSet + describe + drift
  actions, scoped to `stack/<service>-*`, plus narrow S3 read and
  `iam:PassRole`.
- **Outputs** — bucket name, bucket ARN, role ARN, each with an `Export.Name`.

### 2. `cfn/<service>-network-<env>.yaml`

- 3 public + 3 private subnets, `AvailabilityZone: !Select [n, !GetAZs ""]`,
  CIDRs from `!Cidr [!Ref VpcCidr, 6, 8]`.
- IGW + attachment; a shared public route table with `0.0.0.0/0 -> IGW`.
- A `Condition` gating the NAT-Gateway count: one in dev, one per AZ in
  staging/prod, with matching private route tables.
- An application security group: ingress on the app port from the VPC CIDR
  only — **never `0.0.0.0/0`** — and enumerated egress.
- **Outputs** — `VpcId`, `PublicSubnets` and `PrivateSubnets` (comma-joined
  via `!Join`, because a CFN export cannot hold a List), `AppSgId`.

### 3. `cfn/<service>-app-<env>.yaml`

- Consumes the network **only** via `!ImportValue`. Never a copied subnet id.
- `!Split [",", !ImportValue ...]` to turn the joined export back into a list.
- An RDS instance: `StorageEncrypted: true`, `PubliclyAccessible: false`, a
  DB security group taking ingress from the app SG **by id**, not by CIDR.
- Master credentials in Secrets Manager, resolved with
  `{{resolve:secretsmanager:...}}`.

### 4. `cfn/<service>-artifacts-<env>.yaml`

The hardened artefact bucket: PAB ×4, KMS, versioning, a lifecycle transition
to `STANDARD_IA` then `GLACIER_IR`, and a deny-non-TLS bucket policy.

### 5. `.github/workflows/cfn-validate.yml`

`cfn-lint`, then `cfn_nag_scan --fail-on-warnings`, then
`aws cloudformation validate-template` per file, on every PR touching `cfn/`.

## Non-negotiables — check every generated file against these

1. **Every data resource carries BOTH `DeletionPolicy: Retain` AND
   `UpdateReplacePolicy: Retain`.** They cover different destruction paths and
   neither implies the other: the first covers `delete-stack`, the second
   covers an update that changes an immutable property and would replace the
   resource. S3 buckets, RDS instances, Secrets Manager secrets.
2. **No `Action: '*'` and no `Resource: '*'`.** Every IAM policy enumerates
   its actions and scopes to a specific ARN or ARN prefix.
3. **`iam:PassRole` is never unscoped.** It is the classic escalation path.
4. **No database password as a Parameter**, `NoEcho: true` or not. NoEcho
   masks console output; the value still crosses the API and sits in the
   ChangeSet. Use a Secrets Manager dynamic reference.
5. **Every Parameter has a `Description`. Every Condition has a comment
   explaining what it gates.**
6. **Deploy via ChangeSet only.** Emit `create-change-set` →
   `describe-change-set` → `execute-change-set` in the file header. Never
   `aws cloudformation deploy`.

## Known quirks — the audit checklist

This generator, like most convention-following ones, has three failure modes
worth checking for explicitly. An auditor should look for each by name and
record it as *not observed* rather than manufacturing it if it did not occur.

1. **`StringLike` on the OIDC `aud` claim where `StringEquals` is required.**
   The audience is a single literal (`sts.amazonaws.com`) — there is no
   pattern to match, so `StringLike` is strictly weaker for no benefit.
   `StringLike` belongs only on `sub`, where the branch/PR ref genuinely
   varies, and even there the org/repo prefix must stay exact.
2. **`NoEcho: true` on a password Parameter** where Secrets Manager dynamic
   resolution is required. See non-negotiable 4.
3. **`DeletionPolicy: Retain` without its `UpdateReplacePolicy: Retain`
   partner.** The pair travels together. Retain on only the first still loses
   the data on a rename.
