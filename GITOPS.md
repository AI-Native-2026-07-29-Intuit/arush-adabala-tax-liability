# GITOPS.md — how GitOps deploys this service, and what overrode the defaults

> **No `taxcalc-api/` subdirectory, again.** The W6 D2 spec names this file `taxcalc-api/GITOPS.md`. It lives at the repository root instead, for the same reason `.github/PIPELINE.md` and `docker/SECURITY.md` do — this repository *is* `taxcalc-api`, and a subdirectory named after the repo would nest every path one level deeper for no gain. Recorded in the README's Week 5 Day 1 and Week 6 Day 1 sections as well.

## Repo layout (after W6 D2)

Two repositories, one boundary between them.

- **`AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability`** — the application repo. Java source, `Dockerfile`, `ci.yml`, `_build-and-push.yml`, `_bump-config.yml`. **Holds no cluster credentials**, and did not hold any before today either: `grep -RIn 'kubeconfig\|KUBECONFIG' .github/` returns zero.
- **`AI-Native-2026-07-29-Intuit/arush-adabala-tax-liability-config`** — the GitOps config repo. `base/` holds the W5 D3 manifests verbatim; `overlays/dev/`, `overlays/staging/`, `overlays/prod/` hold the Kustomize overlays; `argocd/projects/taxcalc.yaml`, `argocd/applications/taxcalc-api-dev.yaml` and `argocd/applicationsets/taxcalc-api-envs.yaml` live there; `platform/` holds the things Argo CD is deliberately *not* allowed to manage.

## Reconcile loop (the GitOps half)

1. CI builds and pushes `uptimecrew/taxcalc-api:<sha>` to GHCR (and ECR when an AWS account is wired up).
2. CI's `call-bump-config` job opens a PR against the config repo bumping `overlays/dev/kustomization.yaml`'s image tag. **This job cannot reach the cluster** — `contents: read` on this repo plus a fine-grained PAT scoped to the config repo, and nothing else.
3. A human merges that PR. **Merging is what deploys.**
4. Argo CD's `application-controller`, running *inside* the cluster, polls the config repo roughly every 3 minutes and sees the new SHA on `main`.
5. It renders `overlays/dev` with Kustomize, diffs against live cluster state, and applies the difference server-side. `taxcalc-dev` converges within ~30 seconds for a single Deployment image bump.
6. Argo CD updates the Application's status. `argocd-notifications-controller` observes; if the sync phase flips to `Failed` or health to `Degraded`, `#taxcalc-deploys` gets an alert. There is deliberately **no** `on-sync-succeeded` trigger.

Rollback is `git revert` on the config repo. Drift is a controller alarm, not a discovery.

## Drift behaviour — measured, and the obvious experiment does not work here

The dev, staging and prod Applications all carry `automated.selfHeal: true`.

**The experiment the spec suggests proves nothing against this Application set.** `kubectl -n taxcalc-prod scale deployment taxcalc-api --replicas=5` was *not* reverted, and that is correct: `/spec/replicas` is in `ignoreDifferences` on every Application here. Git owns the value the Deployment is **created** with; the HorizontalPodAutoscaler owns it from then on. Without the ignore, `selfHeal` and the HPA overwrite each other forever — the Application never settles on `Synced`, and the notifications wired up in Task 4 alert on a fight that is working as designed. It is the one field where "Git is the source of truth" is simply wrong, so the drift experiment has to target a field Argo CD actually owns.

**The ignore also hid a real conflict, which is the cost of having it.** `base/50-taxcalc-api.hpa.yaml` originally set `minReplicas: 2` for every environment while the overlays set 1 / 2 / 3. In dev the HPA pulled the Deployment straight back up to 2 — and because the field is ignored, **Argo CD reported `Synced` throughout**. Git said 1, the cluster ran 2, nothing flagged it, and Task 1's `1/1 replicas` Done-When silently failed. This is the W5 D3 `replicas`-vs-`minReplicas` conflict surviving into GitOps and getting *quieter*, because reconciliation papers over it. Each overlay now patches the HPA floor to match the replica count it asks for (dev 1, staging 2, prod 3), so the two agree at rest while the HPA keeps ownership above the floor. Anything under `ignoreDifferences` needs an independent check that the ignored field actually holds the value Git asks for; `Synced` will not tell you.

**A ConfigMap value does the job.** `LOGGING_LEVEL_ROOT` is `WARN` in `overlays/prod`:

```bash
kubectl -n taxcalc-prod patch cm taxcalc-api-config \
  --type=merge -p '{"data":{"LOGGING_LEVEL_ROOT":"TRACE"}}'
kubectl -n taxcalc-prod get cm taxcalc-api-config \
  -o jsonpath='{.data.LOGGING_LEVEL_ROOT}'
# reverts to WARN on the next reconcile
```

This is intentional. **If a 3am incident fix needs to stick, commit it to the config repo — do not `kubectl edit` it.** An edit that survives is an edit Argo CD has not noticed yet, not an edit that won.

## Notifications — verified as far as they can honestly be verified

**No Slack incoming-webhook was available in this session**, so `argocd-notifications-secret` holds a syntactically valid but non-routable placeholder. Delivery to Slack is therefore **not** claimed. Everything upstream of delivery is verified against a real induced failure rather than asserted.

A deliberate bad merge into the watched branch — `uptimecrew/taxcalc-api:0.0.0-does-not-exist` on the dev overlay — drove `taxcalc-api-dev` to `Degraded` at `19:27:48Z` (the Deployment's `progressDeadlineSeconds: 600` is what sets that delay, and `maxUnavailable: 0` is why the old pod kept serving throughout). Two seconds later:

```
19:27:50Z info   Trigger on-sync-failed result:      [{... [app-sync-failed]     false}]  resource=argocd/taxcalc-api-dev
19:27:50Z info   Trigger on-health-degraded result:  [{... [app-health-degraded] true }]  resource=argocd/taxcalc-api-dev
19:27:50Z info   Sending notification about condition 'on-health-degraded...' to '{slack taxcalc-deploys}'
                 using the configuration in namespace argocd  resource=argocd/taxcalc-api-dev
19:27:50Z error  Failed to notify recipient {slack taxcalc-deploys} defined in resource
                 argocd/taxcalc-api-dev: Post "https://slack.com/api/chat.postMessage": ...
```

Four separate things are confirmed by those lines, and none of them needs a working webhook: the trigger **evaluated true** on a real degradation; the default subscription's `team=taxcalc` selector **matched** and resolved the recipient to `slack:taxcalc-deploys`; the controller **attempted delivery**; and `on-sync-failed` correctly stayed `false` — the sync itself *succeeded*, and it was health that degraded, so the two triggers discriminate rather than both firing on any bad news.

**Rollback was `git revert`, and nothing touched the cluster.** Reverting the bad commit on the config repo restored `Healthy` at `19:30:29Z` — 13 seconds after the revert reached `main`.

## Project-scoped RBAC (who can sync what)

- `proj:taxcalc:developers` — `get` + `sync` on `taxcalc-api-dev` and `taxcalc-api-staging`. **Cannot** sync prod. **Cannot** delete any Application.
- `proj:taxcalc:releasers` — `get` + `sync` on every Application in the project, including prod. **Cannot** delete any Application.

Both roles carry an explicit `deny` on `delete` because deleting an Application whose finalizer prunes its resources is the fastest way to remove a running environment, and it is not an operation either role needs: rollback is `git revert`, and retiring an environment is a change to the ApplicationSet's element list.

The platform team's `argocd-rbac-cm` maps these to OIDC group claims; the group names `uptimecrew:taxcalc-engineers` and `uptimecrew:taxcalc-engineers-releasers` must match the IdP side exactly. **This cluster has no OIDC provider wired to Argo CD**, so the `groups:` bindings are currently inert — they document intent for the platform team rather than granting anything today. W6 D3's shared capstone cluster provides the OIDC layer.

## The AppProject guardrails, and proof they actually deny

`kubectl get appproject taxcalc -o yaml` proves the YAML says the right words. It does not prove the controller enforces them, and those are different claims — Argo CD validates `destinations` and `sourceRepos` when an Application spec is written, but the resource allow-lists only at **sync** time.

`scripts/verify-appproject-guardrails.sh` in the config repo asserts five deny paths plus a positive control. All six pass:

```
PASS  destinations: kube-system refused -> application destination server
      'https://kubernetes.default.svc' and namespace 'kube-system' do not
      match any of the allowed destinations in project 'taxcalc'
PASS  sourceRepos: argocd-example-apps refused
PASS  resource allow-list: Namespace refused
PASS  resource allow-list: ResourceQuota refused
PASS  resource allow-list: LimitRange refused
PASS  positive control: taxcalc-api-dev is Synced/Healthy

==> 6 passed, 0 failed
```

The positive control is what stops the script from passing by refusing *everything*: a deleted project, an empty `destinations` list or a typo'd `sourceRepos` would otherwise score 5/5 and look perfect. Run against a deliberately permissive scratch AppProject, the five deny checks all FAIL and only the control passes — so the script can fail.

## Six things that were measured rather than assumed, and changed the YAML

**1. A placeholder Secret in a reconciled manifest set is worse than no Secret at all.** The W5 D3 `40-taxcalc-api.secret.yaml` carried `replace-at-apply-time-from-secrets-manager`, and under `kubectl apply -f manifests/` that placeholder was *inert* — CI reseeded the Secret from a real store after applying, so the last writer held a real password. Continuous reconciliation deletes that ordering. On the first sync Argo CD wrote the placeholder over the seeded value and every api pod began failing `FATAL: password authentication failed for user "taxcalc_dev"`. With `selfHeal: true`, re-seeding by hand survives exactly one reconcile interval and the failure returns a few minutes later — materially harder to debug than failing immediately. The file moved to the config repo's `platform/secret/`, applied out-of-band.

**2. Re-seeding that Secret with `kubectl apply` is not enough.** `apply` **merges**, so the `app.kubernetes.io/instance` tracking label from the earlier sync survived. On the next reconcile the controller saw a resource it still believed it owned that was no longer in Git, and **pruned** it — pods went straight to `CreateContainerConfigError: secret "taxcalc-api-secrets" not found`. It has to be `delete` then `create`, so the object is untracked.

**3. `preserveResourcesOnDeletion: true` did nothing, because the template also carried the finalizer.** That setting is not a flag checked at deletion time — it works by **omitting** `resources-finalizer.argocd.argoproj.io` from the generated Applications, so an explicit `finalizers:` block in the ApplicationSet template silently overrides it. Removing the `staging` element from the list generator deleted the Application **and every resource behind it**: `kubectl -n taxcalc-staging get deploy,pods` returned `No resources found` within 25 seconds while the setting claimed they would be kept. With the finalizer dropped from the template, the same edit leaves the workload running (2/2 ready) and merely stops managing it — and restoring the element re-adopts the *same pods* with no restart. The standalone `argocd/applications/taxcalc-api-dev.yaml` keeps its finalizer: deleting *that* is a decommission, not a refactor.

**4. Argo CD's `in-cluster` default is not backed by a Secret**, so a `clusters` generator label selector has nothing to match. A selector that matches nothing generates **zero** Applications and reports success — Healthy ApplicationSet, green dashboard, nothing deployed. The cluster is now registered declaratively as a labelled Secret carrying `uptimecrew.example.internal/tier=workload`.

**5. The textbook `on-sync-failed` trigger throws on Applications that have never synced.** Written as `app.status.operationState.phase in ['Error','Failed']`, it logs `failed to execute when condition: cannot fetch phase from <nil>` and the trigger silently does not evaluate for that Application — observed on `taxcalc-api-staging` seconds after the first controller restart. The window in which it silently does not evaluate is exactly the window in which a brand-new environment is most likely to fail its first sync. Both triggers now carry a `!= nil` guard.

**6. Applying the ApplicationSet over the existing standalone `taxcalc-api-dev` adopted it rather than racing it.** The controller set an `ownerReference` on the existing object instead of creating a second one, so the spec's "delete the standalone Application" step was already satisfied. The file stays in the repo as the one concrete, non-templated Application a new contributor can read.

**7. `service.slack` wants a bot token, not an incoming-webhook URL — and the spec says webhook.** The deliverable instructs you to create the secret as `--from-literal=slack-token=$SLACK_WEBHOOK_URL`. `service.slack` is the Slack *API* integration: the controller sends `slack-token` as a bearer credential to `https://slack.com/api/chat.postMessage` and takes the channel from `recipients: [slack:taxcalc-deploys]`. A webhook URL placed there is never requested as a URL at all — it is sent as a token and rejected. The controller's own outbound request during the failure injection is the proof, and the URL in the error is the whole finding:

```
Failed to notify recipient {slack taxcalc-deploys} defined in resource
argocd/taxcalc-api-dev: Post "https://slack.com/api/chat.postMessage": ...
```

An incoming webhook needs `service.webhook.<name>` with a `url:` field and `recipients: [<name>]` — a different service type and a different subscription entry.

**8. `CreateNamespace=true` is denied by `clusterResourceWhitelist: []`, so it does nothing here.** This one was an assumption stated as fact in four files, and it was false. Argo CD implements the option by **injecting a Namespace into the sync task list**, and that injected resource is checked against the project's `clusterResourceWhitelist` like any other. Measured with a scratch AppProject carrying the identical `[]` deny, pointed at a namespace that did not exist:

```
Namespace  taxcalc-nsproof  SyncFailed  resource :Namespace is not permitted in project taxcalc-nsproof
Phase:     Failed
$ kubectl get ns taxcalc-nsproof
Error from server (NotFound): namespaces "taxcalc-nsproof" not found
```

Not "created but unmanaged" — **not created at all**, and the sync fails rather than degrading quietly. The design never actually depended on it: `platform/00-namespaces.yaml` had pre-created all three namespaces before any Application synced, so nothing ever exercised the path and the false claim survived. That is exactly how an assumption gets to look verified.

**The operational consequence is real:** namespaces for this project are strictly platform-provisioned, and **a new environment must be added to `platform/00-namespaces.yaml` before it is added to the ApplicationSet's element list**, or its first sync fails. The option stays in the syncOptions because it becomes correct the instant the project is granted `{group: "", kind: Namespace}`, and because deleting it would invite the next person to assume namespaces are self-provisioning.

This also settles the `base/` question for good. The reference layout wants `00-namespace.yaml` copied into `base/` **and** `clusterResourceWhitelist: []`. Those two instructions cannot both be satisfied — with the empty whitelist, the Namespace is refused whether it arrives as a manifest **or** as `CreateNamespace=true`'s injected resource. The grading rubric names `clusterResourceWhitelist: []` explicitly, so that is the instruction kept; the Namespace lives in `platform/`, and the contradiction is documented rather than silently resolved.

**A regression test exists for this**, because a comment is not a guarantee: `scripts/verify-appproject-guardrails.sh` in the config repo asserts the `Namespace` deny as one of its five deny paths, so anyone who "fixes" the whitelist to make `CreateNamespace` work will see the check flip and have to make the decision consciously.

## Sync waves — two axes, deliberately distinct

- **Within one Application** (`base/kustomization.yaml`): the ConfigMap and the postgres/redis/mongo Deployments are wave `-1`; everything else is wave `0`. Argo CD does not advance to wave 0 until every wave `-1` resource is Synced *and* Healthy, so a first sync into an empty namespace does not create the Deployment until Postgres is Running. Without it the pods come up, fail the datasource check, and crash-loop through the startupProbe's 150-second grace while an operator watches an unexplained `Degraded`.
- **Across Applications** (the ApplicationSet template): dev `0`, staging `1`, prod `2`, so one sync of the whole set lands dev before staging before prod.

These are set with **per-resource patches, not `commonAnnotations`**. `commonAnnotations` stamps every resource with the same number, and a wave every resource shares orders nothing.

## Installing Argo CD behind a TLS-intercepting proxy

Two failures worth recording, because neither is in any tutorial and both look like "Argo CD is broken".

**Every pod sat in `ImagePullBackOff`.** The k3d nodes could not verify `quay.io`'s certificate — `x509: certificate signed by unknown authority` — because the corporate proxy (Zscaler) re-signs TLS and the node image does not carry its root CA. The host Docker daemon *does* trust it, so the images were pulled on the host and `k3d image import`-ed. That alone was **not** enough: Argo CD's install manifest sets `imagePullPolicy: Always` on every container, so a pre-seeded node image store is ignored. Patching the seven workloads to `IfNotPresent` is what actually started them. `k3d image import -c <cluster>` also imported into the server node only here; the agent nodes needed explicit `-n` flags.

**Then the repo-server could not clone from GitHub**, with the same `x509` error surfacing as an Argo CD `ComparisonError` rather than as a TLS problem:

```
Failed to load target state: failed to generate manifest for source 1 of 1:
rpc error: code = Unknown desc = Get "https://github.com/.../info/refs?service=git-upload-pack":
tls: failed to verify certificate: x509: certificate signed by unknown authority
```

Fixed by putting the proxy's CA chain into `argocd-tls-certs-cm` keyed by hostname (`github.com`) and restarting the repo-server — Argo CD's own mechanism for this, and preferable to `insecure: true` on the repository, which would disable verification rather than supply the missing trust anchor.

## What this layer does NOT do (yet)

- **Sealed Secrets / External Secrets Operator** — W6 D3 wires ESO + IRSA against AWS Secrets Manager. Until then the taxcalc-api Secret is seeded out-of-band and lives outside the manifest set entirely.
- **Argo Rollouts (canary / blue-green)** — W6 D5 lands the `Rollout` CR + `AnalysisTemplate` gated on W5 D5's p99 SLI.
- **Multi-cluster ApplicationSet** — the matrix generator's `clusters` selector matches one cluster today; the same template scales to N labelled clusters with no change.
- **Prod sync auto-promotion** — this assignment leaves prod on the same `automated` stanza as dev and staging, because a k3d cluster has no real users. In a real install, consider a `templatePatch` on `env: prod` to drop `automated` and require manual sync. The AppProject's weekend `syncWindows` deny block is the only thing gating prod today — and it fired for real: this work ran on a Friday after 17:00 UTC, so `taxcalc-api-prod` came up `OutOfSync` with `SyncWindow: Manual Allowed` / `Assigned Windows: deny:0 17 * * 5:60h`, and reached `Synced` only through the window's documented `manualSync: true` escape hatch.
- **One manifest set, not two.** `manifests/` still exists in this repository as the W5 D3 artefact, and `k8s-ci.yml` still validates it by applying it to an **ephemeral in-runner k3d cluster** (it authenticates to no standing cluster, which is why the credential grep is zero). The config repo's `base/` is now a second copy of the same YAML, and two copies can drift. The migration is to delete `manifests/` and point `k8s-ci.yml` at the config repo; it is deliberately out of scope for W6 D2 and recorded here rather than left for someone to discover.

## AI deliverable — `argocd-author` Skill audit notes

**The `argocd-author` Claude Skill this deliverable names was not available in this session's tool listing.** The available-skills list contained `design`, `dataviz`, `artifact-*`, `update-config`, `keybindings-help`, `code-review`, `simplify`, `fewer-permission-prompts`, `loop`, `schedule`, `claude-api`, `run`, `init` and `security-review` — no `argocd-author`, and invoking a skill that is not listed is a guess at a name. This is the second deliverable where the named authoring Skill was absent; W6 D1's `github-actions-author` was missing the same way, and the README's Week 6 Day 1 section records it.

The seven artefacts were therefore hand-authored directly against the cohort checklist, and the checklist's own three named "common quirks" were audited for explicitly rather than assumed absent:

**Quirk 1 — `spec.project: default` on a generated Application. Checked, and it is the check that matters most.** Every Application here sets `spec.project: taxcalc`; `kubectl -n argocd get app -o jsonpath='{.items[*].spec.project}'` returns `taxcalc taxcalc taxcalc`. An Application that lands in `default` is completely unguarded — `default` permits `*` for source repos, destinations *and* cluster-scoped resources — so this quirk produces a green dashboard with no guardrail at all, which is strictly worse than an obvious failure.

**Quirk 2 — a missing `resources-finalizer.argocd.argoproj.io`. Audited, and deliberately resolved BOTH ways.** The standalone `taxcalc-api-dev.yaml` and the `AppProject` carry it; the ApplicationSet template deliberately does **not**, because the finalizer defeats `preserveResourcesOnDeletion: true` (finding 3 above). Treating "add the finalizer everywhere" as a blanket rule would have reintroduced the exact bug the experiment found.

**Quirk 3 — a `Rollout` CR scaffolded before Argo Rollouts is installed.** None was written. Argo Rollouts is not installed on this cluster; a `Rollout` object would be rejected by the API server for want of its CRD, and it would also be denied by this project's `clusterResourceWhitelist: []`/`namespaceResourceWhitelist` in any case. W6 D5 lands the install.

### One suggestion accepted, and one rejected

**Accepted — the reference layout's `syncOptions` block, in full.** `CreateNamespace=true`, `ServerSideApply=true`, `PrunePropagationPolicy=foreground`, `PruneLast=true` and `ApplyOutOfSyncOnly=true` were taken as given rather than trimmed to the two that were obviously needed. `ServerSideApply=true` earned it immediately: the taxcalc-dev resources already existed from W5 D3's client-side `kubectl apply`, carrying `last-applied-configuration` annotations, and server-side apply is what let Argo CD adopt them cleanly with no field-manager conflict and no `--force`.

`CreateNamespace=true` was accepted for a reason that turned out to be **wrong**, and finding that out is finding 8 below — it is inert under this project, not load-bearing. It is kept because it is the correct setting the moment the project is granted the `Namespace` kind, and because removing it would make the *next* person assume namespaces are self-provisioning.

**Rejected — the reference layout's `1f1f1f1f1f1f...` image-tag placeholder.** The overlays commit a real tag (`0.2.0`) that exists in the cluster's image store. A placeholder tag resolves to `ImagePullBackOff`, which makes the Application permanently `Degraded` — and that is not merely untidy: Task 4's deliberate-failure experiment depends on being able to tell a *caused* failure apart from ambient noise, and an Application that is already Degraded for an unrelated reason destroys the signal the experiment is trying to produce. The placeholder exists in the reference so that `_bump-config.yml` has something to rewrite; a real tag serves that purpose identically, since `kustomize edit set image` replaces whatever is there.
