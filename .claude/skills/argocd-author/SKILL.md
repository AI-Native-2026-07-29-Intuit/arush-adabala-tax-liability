---
name: argocd-author
description: Scaffold a complete Argo CD GitOps layout for a service - AppProject, Application, ApplicationSet, Kustomize base and per-environment overlays, notifications ConfigMap, ExternalSecret, and an Argo Rollouts CR for canary or blue-green strategies. Use when asked to author, scaffold, or independently review an Argo CD app-of-apps layout for a service.
---

# argocd-author

## Provenance — read this before trusting the output

This skill was **authored locally**, not distributed by the course. The W6 D2
deliverable names `/argocd-author` as a provided tool; it was absent from the
session's skill listing, as `github-actions-author` was for W6 D1. It is
written here from standard Argo CD conventions and the cohort reference
layout so the audit pass the deliverable asks for has something to audit.

That has a consequence for how much weight its disagreements carry. A
generator written by the same person who wrote the artefacts under review is
a weaker check than an independent one — it will tend to agree. **Under-
disagreement is the expected failure mode, so a clean diff is not evidence
the artefacts are correct.** Any audit citing this skill should say so.

## Arguments

```
/argocd-author <service-name> [--namespace-prefix <p>] [--strategy <s>] [--secrets-mode <m>]
```

Arriving as one raw string; parse it yourself.

| Argument | Values | Default |
|---|---|---|
| `<service-name>` | positional, required | — |
| `--namespace-prefix` | any DNS label | the service name |
| `--strategy` | `rolling`, `canary`, `blue-green` | `rolling` |
| `--secrets-mode` | `none`, `eso`, `sealed` | `none` |

Environments are `dev`, `staging`, `prod` unless told otherwise. Namespaces
are `<namespace-prefix>-<env>`.

## Hard rule: this is a cold pass

**Do not read the repository's existing `argocd/`, `base/`, `overlays/`,
`platform/` or `argocd-system/` files before generating.** Do not read a
GITOPS.md. The entire value of this skill is that its output was produced
without sight of the artefacts it will be compared against; reading them
first collapses the comparison into a restatement.

Derive everything from the arguments, the conventions below, and — only for
facts you cannot invent, such as container port, image repository or health
endpoint — the application's own `Dockerfile` and Kubernetes manifests under
`manifests/`. If a fact is genuinely unavailable, emit a clearly-marked
placeholder rather than guessing silently.

Write output to a scratch location: a `scratch/argocd-author` branch, or a
`--out` directory if one is given. Never overwrite tracked artefacts.

## Artefacts to generate

### 1. AppProject — `argocd/projects/<prefix>.yaml`

Named `<prefix>`. Enumerate rather than wildcard:

- `sourceRepos:` the single config repo URL. Never `'*'`.
- `destinations:` one entry per environment, `server: https://kubernetes.default.svc`, `namespace: <prefix>-<env>`.
- `clusterResourceWhitelist: []` — the project manages no cluster-scoped resources.
- `namespaceResourceWhitelist:` enumerate exactly the kinds the base uses (ConfigMap, Service, Deployment, Ingress, HorizontalPodAutoscaler, ServiceMonitor, and Rollout when `--strategy` is not `rolling`).
- `namespaceResourceBlacklist:` at minimum `ResourceQuota`, `LimitRange`.
- `roles:` a `developers` role with `get`/`sync` on non-prod Applications, and a `releasers` role with `get`/`sync` on all of them. Neither gets `delete`.
- `syncWindows:` a deny window covering the weekend, with `manualSync: true` as the documented escape hatch.
- `metadata.finalizers:` `resources-finalizer.argocd.argoproj.io`.

### 2. Application — `argocd/applications/<service>-dev.yaml`

A single standalone Application for `dev`, as the worked example.

- `spec.project:` the AppProject generated above.
- `spec.source.repoURL` / `targetRevision: main` / `path: overlays/dev`.
- `spec.destination.namespace: <prefix>-dev`.
- `syncPolicy.automated:` `prune: true`, `selfHeal: true`.
- `syncOptions:` `CreateNamespace=true`, `ServerSideApply=true`, `PrunePropagationPolicy=foreground`, `PruneLast=true`, `ApplyOutOfSyncOnly=true`.
- `retry:` `limit: 5`, backoff `5s` → `3m`.
- `metadata.finalizers:` `resources-finalizer.argocd.argoproj.io`, so a deleted Application cascades to its resources.
- `metadata.labels:` `team: <prefix>`, which the notification subscription selects on.

### 3. ApplicationSet — `argocd/applicationsets/<service>-envs.yaml`

A matrix generator over an env list × a cluster generator, templating the
same Application shape across all three environments. Per-env values
(`replicas`, ingress host, log level) come from the list generator's
elements. Carry the same finalizer and the same `team` label into the
template.

### 4. Kustomize base — `base/`

`kustomization.yaml` plus Deployment, Service, ConfigMap, Ingress, HPA and
ServiceMonitor. Conventions:

- Sync waves via `argocd.argoproj.io/sync-wave`: ConfigMap and any datastore at `-1`, workloads at `0`.
- `resources:` requests and limits set on every container.
- Probes: startup, readiness and liveness, pointing at the app's real health endpoint.
- `images:` a placeholder tag in the base, so overlays and CI have something to rewrite.
- Labels follow `app.kubernetes.io/*` recommended keys.

### 5. Overlays — `overlays/{dev,staging,prod}/kustomization.yaml`

Each sets `namespace`, a per-env `replicas` count (1 / 2 / 3), a per-env
Ingress host, ConfigMap patches for `SPRING_PROFILES_ACTIVE` and log level,
and an `images:` tag rewrite. CI rewrites the dev overlay's tag; staging and
prod are promoted by a human.

### 6. Notifications ConfigMap — `argocd-system/notifications-cm.yaml`

- `service.slack:` `token: $slack-token`, resolved from `argocd-notifications-secret`. The Secret is created out-of-band and never committed; emit the `kubectl create secret` command as a comment, not a manifest.
- Triggers: `on-sync-succeeded`, `on-sync-failed`, `on-health-degraded` — the standard Argo CD set.
- A matching `template.*` for each trigger.
- A `subscriptions:` block routing `selector: "team=<prefix>"` to a Slack channel.

Note in a comment that the controller reads this ConfigMap at startup, so
`kubectl apply` must be followed by
`kubectl -n argocd rollout restart deploy/argocd-notifications-controller`.

### 7. Progressive delivery — `base/80-<service>.rollout.yaml`

Generate **only** when `--strategy` is `canary` or `blue-green`.

Replace the Deployment with an `argoproj.io/v1alpha1 Rollout` carrying the
matching `strategy:` block — weighted steps with pauses for canary, an
active/preview Service pair for blue-green — plus an `AnalysisTemplate`
gating promotion on a success-rate or latency metric.

**Scaffold this even when Argo Rollouts is not installed on the target
cluster.** The CR is the correct artefact for the requested strategy; whether
the controller exists yet is a sequencing problem for whoever applies it, and
emitting nothing silently downgrades the strategy the caller asked for.

### 8. Secrets — `platform/secret/`

- `--secrets-mode none`: a placeholder Secret manifest, clearly marked, kept **outside** the synced manifest set.
- `--secrets-mode eso`: a `SecretStore` (or `ClusterSecretStore`) plus an `ExternalSecret` referencing AWS Secrets Manager, authenticating via IRSA. No secret material in Git.
- `--secrets-mode sealed`: a `SealedSecret` plus the `kubeseal` command that produced it.

## Finish by reporting

Print a table of every file written, and beneath it a short list of the
judgement calls that a reviewer would most reasonably disagree with —
placeholder values, anything assumed rather than derived, and any place a
convention above was applied that the caller's situation might not warrant.
That list is what makes the output auditable rather than merely present.
