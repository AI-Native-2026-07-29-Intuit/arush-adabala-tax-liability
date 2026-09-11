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

**The ignore is scoped by name, and W6 D5 found a comment that assumed otherwise.** Each entry names `kind: Deployment` *and* a specific `name`, deliberately — a blanket ignore on the kind would also hide a real, unexplained edit to some other Deployment's spec. So `name: taxcalc-api` never covered `taxcalc-api-worker`, the KEDA scale target added in W6 D5. The worker manifest had omitted `replicas` entirely and justified it by saying the field was already ignored "on every generated Application", which was not true: the omission was the only thing preventing a `selfHeal`-vs-HPA fight, and the comment claiming otherwise was an invitation to add the field and cause one. Both the Application and the ApplicationSet now carry a second entry for `name: taxcalc-api-worker`, and `base/12` states `replicas: 0` outright — the value Git owns at creation, with KEDA owning every value after it.

**A ConfigMap value does the job.** `overlays/dev` patches `LOGGING_LEVEL_ROOT` to `DEBUG`, and unlike `/spec/replicas` that key is not ignored and has no second controller competing for it — Argo CD owns it outright. Run against `taxcalc-api-dev` on **2026-09-04**:

```bash
kubectl -n taxcalc-dev patch cm taxcalc-api-config \
  --type=merge -p '{"data":{"LOGGING_LEVEL_ROOT":"TRACE"}}'
kubectl -n taxcalc-dev get cm taxcalc-api-config \
  -o jsonpath='{.data.LOGGING_LEVEL_ROOT}'
# reverts to DEBUG on the next reconcile
```

**The controller log is the evidence** — `kubectl -n argocd logs statefulset/argocd-application-controller --tail=200 | grep taxcalc-api-dev`:

```
time=19:14:59Z msg="Updated sync status: Synced -> OutOfSync" application=taxcalc-api-dev reason=ResourceUpdated
time=19:14:59Z msg=Syncing application=argocd/taxcalc-api-dev syncId=00018-jVkwK
time=19:14:59Z msg="Tasks (dry-run)" tasks="[Sync/-1 resource /ConfigMap:taxcalc-dev/taxcalc-api-config obj->obj (,,)]"
time=19:15:00Z msg="Adding resource result, status: 'Synced', phase: 'Running', message: 'configmap/taxcalc-api-config serverside-applied'"
time=19:15:00Z msg="Updating operation state. phase: Running -> Succeeded, message: ... -> 'successfully synced (all tasks run)'"
time=19:15:00Z msg="Updated sync status: OutOfSync -> Synced" application=taxcalc-api-dev reason=ResourceUpdated
```

**Detected and healed inside two seconds** — `19:14:59Z` to `19:15:00Z` — against a deliverable that allows three minutes. That gap is worth understanding rather than banking, because the two numbers measure different things. The ~3-minute figure is the **Git polling interval**: how long a change *committed to the config repo* waits before the controller notices it. Drift in the *cluster* takes a different path entirely — the controller watches live resources through an informer, so the patch above lands as a watch event and the `Synced -> OutOfSync` transition is stamped in the same second as the write. Nothing here was polled. An operator who reasons "Argo CD reconciles every 3 minutes, so I have a 3-minute window to test something by hand" has the model backwards: for a live edit there is effectively no window at all.

The `Tasks (dry-run)` line is the one to read closely. It names exactly one task, `Sync/-1 resource /ConfigMap:taxcalc-dev/taxcalc-api-config` — `ApplyOutOfSyncOnly=true` means the heal touched only the drifted ConfigMap and left the Deployment, Service, Ingress and HPA alone. `selfHeal` is not a full re-apply, so a drift repair does not restart pods as a side effect. The `Sync/-1` prefix is the sync wave: the ConfigMap sits in wave `-1` alongside the datastores, which is also why it heals before anything that consumes it would be reconsidered.

This is intentional. **If a 3am incident fix needs to stick, commit it to the config repo — do not `kubectl edit` it.** An edit that survives is an edit Argo CD has not noticed yet, not an edit that won.

## Notifications — verified as far as they can honestly be verified

**Delivery to Slack is verified end to end.** `argocd-notifications-secret` holds a real bot token, created out-of-band and never committed.

**It took a bot token, not the webhook URL the deliverable asks for.** `service.slack` is the Slack *Web API* integration: it sends `slack-token` as a bearer credential to `chat.postMessage` and takes the channel from `recipients:`. A webhook URL placed there is never requested as a URL at all. The spec's `--from-literal=slack-token=$SLACK_WEBHOOK_URL` cannot deliver, and the spec's own rubric asks for both `service.slack` *and* a screenshotted alert — only a bot token satisfies both.

**A second blocker sat behind the credential: Zscaler.** The controller's egress to `slack.com` is TLS-intercepted, and its image does not carry the proxy's root CA, so every send failed with `x509: certificate signed by unknown authority` *before the token was ever evaluated*. `argocd-tls-certs-cm` does not help — that is hostname-keyed and used only for Git. The fix is a CA bundle of the image's own 137 system roots **plus** the Zscaler chain, mounted as a ConfigMap with `SSL_CERT_FILE` pointing at it:

```bash
kubectl -n argocd exec deploy/argocd-notifications-controller -- \
  cat /etc/ssl/certs/ca-certificates.crt > ca-base.crt
cat ca-base.crt zscaler-chain.pem > ca-bundle.crt
kubectl -n argocd create configmap argocd-notifications-ca --from-file=ca-bundle.crt
# + volumeMount at /etc/argocd-ca and env SSL_CERT_FILE=/etc/argocd-ca/ca-bundle.crt
```

Mounting the Zscaler root *alone* and pointing `SSL_CERT_FILE` at it would authenticate Slack and break every other TLS target — the bundle has to be additive.

**The proof, from a real induced sync failure:**

```
00:10:02Z  Sending notification about condition 'on-sync-failed…' to '{slack taxcalc-deploys}'
annotation: notified.notifications.argoproj.io =
  {"on-sync-failed:…:slack:taxcalc-deploys": 1788567002}     # == 00:10:02Z
Failed to notify (since the CA mount): 0
```

The `notified` annotation is the receipt: the controller writes it **only after a successful send**, and it is what deduplicates the alert so a failing app does not re-notify every reconcile. Before the CA fix, that annotation never appeared and `Failed to notify` logged on every pass.

**This run also fired `on-sync-failed`, which the earlier experiment never did.** The bad-image-tag injection degrades *health* while the sync itself succeeds, so it only ever exercised `on-health-degraded`. Committing a resource the AppProject denies (a `Namespace`, under `clusterResourceWhitelist: []`) fails the sync operation itself — and is far faster to observe, since a health degradation waits out the Deployment's `progressDeadlineSeconds: 600` while a sync failure lands as soon as the `retry` budget (`limit: 5`, backoff to 3m) is exhausted. Both triggers are now confirmed to fire on the condition they name, and only on that condition.

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

### The alerting gap this found by accident

The first attempt at the injection above used `git commit -am`, which stages only **tracked** files — so the new manifest was never committed while the `kustomization.yaml` line referencing it was. The result was a render failure, not a sync failure:

```
ComparisonError: Failed to load target state: ... accumulating resources:
  evalsymlink failure on '.../overlays/dev/zz-deliberate-denied-resource.yaml': no such file or directory
```

**Neither trigger fires on that.** `on-sync-failed` reads `app.status.operationState.phase`, and a `ComparisonError` means no sync operation is ever attempted, so `operationState` never changes; `on-health-degraded` reads `app.status.health.status`, and the last-known-good workload is still running happily, so health stays `Healthy`. The Application shows `Sync Status: Unknown` in the UI and **nothing is alerted at all** — a config repo that has stopped rendering looks, to the notification layer, exactly like one with nothing to do.

That is a real gap in the notifications this deliverable specifies, and it is the likeliest real-world failure of the three: forgetting to `git add` a file is a more common mistake than pushing a bad image tag. A third trigger closes it:

```yaml
trigger.on-comparison-error: |
  - description: Argo CD cannot render the desired state.
    send:   [app-sync-failed]
    when:   app.status.conditions != nil and
            app.status.conditions.any(.type == 'ComparisonError')
```

Left unadded here because the deliverable names exactly two triggers and the rubric checks for them — but recorded, because the gap is real and the next person will hit it.

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

**9. `kustomize edit set image NAME=NAME:SHA` silently deletes the registry, and the guard written to catch that passed anyway.** Found by the first real `call-bump-config` run, on the merge that made the workflow live — which is the argument for the `workflow_dispatch` entry point in one sentence. `kustomize edit set image` sets **both** `newName` and `newTag`. The dev overlay pins `newName: ghcr.io/ai-native-2026-07-29-intuit/taxcalc-api`, so passing the bare `uptimecrew/taxcalc-api` as the target reset `newName` to the bare name:

```diff
-  - name:    uptimecrew/taxcalc-api
-    newName: ghcr.io/ai-native-2026-07-29-intuit/taxcalc-api
+- name: uptimecrew/taxcalc-api
+  newName: uptimecrew/taxcalc-api
```

Merging that would have pointed dev at an image that exists in no registry — `ImagePullBackOff` on an Application that was `Healthy` a minute earlier.

**The guard was satisfied *by* the bug.** The step asserted `grep -q "image: <image-name>:<sha>"` against the rendered output, and once `newName` was clobbered the rendered image was exactly `uptimecrew/taxcalc-api:<sha>` — the string being searched for. The check was written to catch a silently-inert edit; it could not catch this one, because this failure makes the assertion *more* likely to match, not less. Third instance of the same shape in this deliverable, after `Synced` with the wrong replica count and the green `CreateNamespace` path: **a check whose passing condition is produced by the failure it is meant to detect.** The fix asserts against the *effective* reference read back out of the file — `newName` if set, else `name` — plus a second check that the effective reference still contains a registry host.

**The rewrite is now surgical rather than `kustomize edit`.** Beyond the `newName` bug, `kustomize edit` re-serialises the entire file: list indentation flattened, map keys alphabetised, the `patches:` block relocated — which detached this repo's comments from the fields they document. `newName` is now read and never written, and only the `newTag:` value changes, preserving indentation and quoting. The bump diff is one line, which is also the only size of diff a human will actually review on a deploy PR.

**10. A closed bump PR leaves its branch behind, and the idempotency guard then suppresses that SHA forever — reporting success each time.** Found immediately after fixing finding 9, while trying to prove the fix. The guard read:

```bash
if git ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1; then
  echo "Branch $BR already exists - nothing to do."; exit 0
fi
```

That conflates two states. Closing a PR does not delete its branch, so after the defective bump PR was closed, dispatching the *fixed* workflow for the same SHA found the orphaned branch, skipped, and **exited green having done nothing**. The evidence that anything was wrong was a single info line in a successful run's log.

**The guard now keys on an open PR rather than a branch** — `gh pr list --head "$BR" --state open`. An open PR means genuinely nothing to do; a branch with no open PR is orphaned and gets deleted and recreated. The no-op check (`git diff --cached --quiet`) moved ahead of both, so a genuinely unchanged overlay never deletes a remote branch on its way to doing nothing.

**This is the fourth occurrence of one pattern in this deliverable, and the pattern is the real finding.** `Synced` with the wrong replica count; a green `CreateNamespace` path that never created a namespace; a grep guard satisfied by the very bug it should have caught; and now a skip-guard whose success condition is produced by the failure it should have flagged. In each case a check passed *because* something was broken, not despite it. The generalisation worth carrying forward: **a check that cannot fail in the situation it is meant to detect is not a weak check, it is an inverted one** — and the way to find them is to induce the failure and confirm the check goes red, which is exactly what `scripts/verify-appproject-guardrails.sh` does by running against a deliberately permissive scratch project.

## Sync waves — two axes, deliberately distinct

- **Within one Application** (`base/kustomization.yaml`): the ConfigMap and the postgres/redis/mongo Deployments are wave `-1`; everything else is wave `0`. Argo CD does not advance to wave 0 until every wave `-1` resource is Synced *and* Healthy, so a first sync into an empty namespace does not create the Deployment until Postgres is Running. Without it the pods come up, fail the datasource check, and crash-loop through the startupProbe's 150-second grace while an operator watches an unexplained `Degraded`.
- **Across Applications** (the ApplicationSet template): dev `0`, staging `1`, prod `2`, so one sync of the whole set lands dev before staging before prod.

These are set with **per-resource patches, not `commonAnnotations`**. `commonAnnotations` stamps every resource with the same number, and a wave every resource shares orders nothing.

## Three places this repo departs from the assignment's literal wording

Each of these is a deviation a reviewer can grep for and not find. All three are deliberate, all three were measured, and in each case following the literal instruction produces a system that does not work. They are collected here so the decision is auditable in one place rather than reconstructed from three file headers.

**1. "Within each overlay, the Namespace resource carries `argocd.argoproj.io/sync-wave: "-1"` via `commonAnnotations`."**

No overlay contains a `Namespace`, and `commonAnnotations` appears nowhere. Two independent reasons:

- *It cannot sync.* The AppProject sets `clusterResourceWhitelist: []` — the guardrail the previous deliverable asks for by name. `Namespace` is cluster-scoped, so an overlay carrying one is refused (`resource :Namespace is not permitted in project taxcalc`) and **the whole sync fails**, not just that resource. Measured against a scratch project with the identical deny; see the `CreateNamespace` finding above.
- *It would not order anything even if it synced.* `commonAnnotations` stamps every rendered resource with the same value. A wave that every resource shares is not an ordering.

What replaces it: the ordering guarantee lives on the resources that actually need it — `base/kustomization.yaml` puts the ConfigMap and the postgres/redis/mongo Deployments in wave `-1` and everything else in wave `0`, so the Deployment is not created until its datastores are Healthy. That is the outcome wave `-1` on a Namespace is reaching for, applied where a first sync can actually race. The Namespaces themselves carry `argocd.argoproj.io/sync-wave: "-1"` in `platform/00-namespaces.yaml`, where the objects live; it is **inert today** (`kubectl apply` ignores sync waves) and becomes live if a platform-owned Application under a project that permits `Namespace` ever manages that directory.

Three checks in `scripts/verify-appproject-guardrails.sh` (7–9) assert that each overlay renders no `Namespace`, precisely because "restoring" it looks like fixing a deviation rather than breaking every sync. **Reverting this deviation means widening the whitelist and flipping four checks of that script** — it is a conscious retraction of the guardrail deliverable, not a one-line correction.

**2. "its own ConfigMap patch (`SPRING_PROFILES_ACTIVE` … `LOG_LEVEL` …)" — the key here is `LOGGING_LEVEL_ROOT`.**

`LOG_LEVEL` is read by nothing in this application. `LOGGING_LEVEL_ROOT` is the key the W5 D3 base ships and the one Spring Boot's relaxed binding maps to `logging.level.root`. Using the literal name would have produced three overlays that differ visibly in Git and identically at runtime — the worst kind of green. The tightening the instruction asks for is intact and observable: `DEBUG` → `INFO` → `WARN`, confirmed in the live ConfigMaps.

**3. "`SPRING_PROFILES_ACTIVE` matches the env" — the value is `k8s,<env>`, not bare `<env>`.**

`application.yml` defines documents for `docker`, `k8s` and `test` only, and the `k8s` document is what supplies the in-cluster Postgres, Redis, Mongo and Kafka coordinates. A bare `dev` profile leaves the app on its default localhost datasource and it never starts. The env-specific half is present and does match the environment; the `k8s` half is what makes the pod boot. `dev` / `staging` / `prod` currently match no document and are there for a future `on-profile:` block.

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

## The cost of running CI's image on the wrong architecture, measured

`overlays/dev` pins the image CI actually pushed — `ghcr.io/ai-native-2026-07-29-intuit/taxcalc-api:ec36057e…`, digest `sha256:b1b3c2e7…`, byte-identical to what the GitHub packages API reports for that tag. Two blockers stood in the way and both are worth stating, because the second one is invisible until a pod refuses to start:

1. **The GHCR package is private**, and org policy blocks changing package visibility (recorded in `.github/PIPELINE.md` on W6 D1). A real cluster cannot pull it without an `imagePullSecret` holding a long-lived PAT — which sits badly beside the OIDC premise this pipeline just established. The k3d lab sidesteps it by **side-loading**: pull on the host, where Docker is already authenticated, then `k3d image import` into the node image stores so the kubelet never contacts a registry. `imagePullPolicy: IfNotPresent` in the base is what makes that work.
2. **CI publishes `linux/amd64` only**, and k3d nodes on Apple Silicon are `arm64`. It runs anyway because the Rancher Desktop VM has binfmt registered, so containerd executes it under emulation — verified with a throwaway pod *before* committing the change rather than assumed (`java -version` from the image's own jlinked JRE returned Temurin 21.0.12 on an arm64 node).

**Emulation is not free, and the number is larger than "a bit slower".** Same application, same manifests, **both at rest** (three samples, 20s apart, after the HPA had settled):

| environment | image | CPU (steady state) | memory |
|---|---|---|---|
| `taxcalc-dev` | amd64 CI image, emulated | **56–62m** | 796Mi |
| `taxcalc-staging` | arm64 local build, native | **6–8m** | 479–497Mi |

Roughly **8–9× the idle CPU** and ~1.6× the memory, for identical work.

**The first version of this table said 14×, and it was wrong** — those samples were taken while the rollout was still settling, not at rest, so they measured the startup spike rather than steady state. Re-measured after the autoscaler stabilised. The lesson generalises past this table: a resource measurement taken during a rollout is a measurement of the rollout.

The spike is real, though, and it has a visible consequence. It pushed `taxcalc-dev` past the HPA's 70%-of-request target and the autoscaler scaled 1 → 2 (`New size: 2; reason: cpu resource utilization (percentage of request) above target`), then scaled back to 1 about **390 seconds** later — the 300s `scaleDown.stabilizationWindowSeconds` from W5 D3 plus a reconcile. So **`1/1` does hold at rest**; the deliverable's two Task 1 checks are not in permanent conflict, they just cannot both be observed in the ~6 minutes after a deploy.

It also matters downstream: **W6 D5 gates a canary on the p99 latency SLI from W5 D5.** Measuring p99 against an emulated binary would produce a number that says nothing about production. The real fix is a multi-arch build (`platforms: linux/amd64,linux/arm64`) in `_build-and-push.yml`, which is not free here — the Dockerfile's `builder` stage runs Gradle and the `jlink-builder` stage builds a custom JRE, both slow under QEMU. Recorded as a documented trade-off rather than smuggled into this deliverable.

## What this layer does NOT do (yet)

- **Sealed Secrets / External Secrets Operator** — W6 D3 wires ESO + IRSA against AWS Secrets Manager. Until then the taxcalc-api Secret is seeded out-of-band and lives outside the manifest set entirely.
- **Argo Rollouts (canary / blue-green)** — W6 D5 lands the `Rollout` CR + `AnalysisTemplate` gated on W5 D5's p99 SLI.
- **Multi-cluster ApplicationSet** — the matrix generator's `clusters` selector matches one cluster today; the same template scales to N labelled clusters with no change.
- **Prod sync auto-promotion** — this assignment leaves prod on the same `automated` stanza as dev and staging, because a k3d cluster has no real users. In a real install, consider a `templatePatch` on `env: prod` to drop `automated` and require manual sync. The AppProject's weekend `syncWindows` deny block is the only thing gating prod today — and it fired for real: this work ran on a Friday after 17:00 UTC, so `taxcalc-api-prod` came up `OutOfSync` with `SyncWindow: Manual Allowed` / `Assigned Windows: deny:0 17 * * 5:60h`, and reached `Synced` only through the window's documented `manualSync: true` escape hatch.
- **One manifest set, not two.** `manifests/` still exists in this repository as the W5 D3 artefact, and `k8s-ci.yml` still validates it by applying it to an **ephemeral in-runner k3d cluster** (it authenticates to no standing cluster, which is why the credential grep is zero). The config repo's `base/` is now a second copy of the same YAML, and two copies can drift. The migration is to delete `manifests/` and point `k8s-ci.yml` at the config repo; it is deliberately out of scope for W6 D2 and recorded here rather than left for someone to discover.

## AI deliverable — `argocd-author` Skill audit notes

### Provenance — read this before weighing the findings

**The `argocd-author` Skill was not distributed by the course.** It was absent from the session's skill listing, exactly as W6 D1's `github-actions-author` was (recorded in the README's Week 6 Day 1 section). It was therefore **authored locally** at `.claude/skills/argocd-author/SKILL.md`, written to standard Argo CD conventions and the cohort reference layout, and then run:

```
/argocd-author taxcalc-api --namespace-prefix taxcalc --strategy canary --secrets-mode eso
```

Output is on the config repo's `scratch/argocd-author` branch under `.argocd-author-out/`, mirroring the real paths so each artefact diffs against its counterpart. The branch is never merged.

**A generator written by the same person who wrote the artefacts under review is a weaker check than an independent one, and it will tend to agree.** That caveat is in the SKILL.md's own first section, not just here. Two consequences worth stating rather than hiding:

- **The pass was not fully cold.** The SKILL.md carries a hard rule against reading `argocd/`, `base/`, `overlays/` or `argocd-system/` before generating, and generation wrote to a separate output tree so no existing file was read to write it. But the author of the skill had already read those files. The rule constrains the procedure, not the memory behind it.
- **A clean diff would therefore not have been evidence of correctness.** The deviations below are worth something because they exist; their *absence* would have proven nothing.

### What the generated output actually disagreed about

Six substantive deviations, from `diff -u` per artefact. Comment-only differences are excluded — the generated files are terse and the hand-written ones are heavily commented, which accounts for most of the raw diff and none of the meaning.

| # | Field | Generated | Committed | Correct |
|---|---|---|---|---|
| 1 | ApplicationSet template `finalizers` | present | omitted, with `preserveResourcesOnDeletion: true` | **committed** |
| 2 | `ignoreDifferences` on `/spec/replicas` | absent | present | **committed** |
| 3 | `syncPolicy.automated.allowEmpty` | absent | `false` | **committed** |
| 4 | `trigger.on-sync-succeeded` | present, and subscribed | omitted deliberately | **committed** |
| 5 | dev overlay image | `1f1f1f…` placeholder, no `newName` | real SHA + GHCR `newName` | **committed** |
| 6 | `Rollout` + `AnalysisTemplate` | scaffolded | absent | **generated** |

**1 is the one that would have caused real damage.** The generator carried `resources-finalizer.argocd.argoproj.io` into the ApplicationSet template — the textbook rule, applied uniformly. It is wrong here for a non-obvious reason: `preserveResourcesOnDeletion: true` is not a flag the controller reads at deletion time, it works *by withholding that finalizer*. An explicit `finalizers:` block in the template silently overrides it, and dropping an env from the `elements:` list would then tear down a running environment on the next reconcile. The failure is invisible until the day it deletes prod. This is the strongest argument in the whole audit for reading generated YAML rather than applying it: the generator was following good general practice, and good general practice is what breaks this file.

**4 is a case where the generator followed upstream and the deliverable overrides it.** `on-sync-succeeded` is part of Argo CD's stock trigger catalogue, so a convention-following generator emits it. Subscribing every Application to it produces the deploy-firehose the spec forbids — and the failure mode is that somebody mutes the channel, which silences the failures too.

**6 is the one the generator won, and it is quirk 3 behaving exactly as the checklist predicts.** `--strategy canary` produced `base/80-taxcalc-api.rollout.yaml` with a weighted canary and a Prometheus `AnalysisTemplate`, plus the `Rollout`/`AnalysisTemplate` kinds added to the AppProject whitelist. Argo Rollouts is not installed, so applying it today fails for want of the CRD — but that is a sequencing problem, not a wrong artefact, and emitting nothing would have silently downgraded the strategy that was asked for. Per the checklist it is **commented out with a `# W6 D5 lands this` note** rather than deleted.

### The two quirks that did not appear

**Quirk 1 — `spec.project: default`. Not observed.** The generated Application and ApplicationSet template both set `spec.project: taxcalc`. Recorded as *not observed* rather than manufactured: a generator instructed to create an AppProject and reference it will not usually forget to. The committed artefacts are independently verified anyway — `kubectl -n argocd get app -o jsonpath='{.items[*].spec.project}'` returns `taxcalc taxcalc taxcalc` — because this is the quirk with the worst blast radius. `default` permits `*` for source repos, destinations *and* cluster-scoped resources, so an Application that lands there is unguarded while the dashboard stays green.

**Quirk 2 — a missing finalizer. Observed inverted.** The generator did not omit the finalizer; it added one where it must not be (deviation 1). The checklist frames this quirk in one direction only, and the opposite error is the more dangerous of the two: a *missing* finalizer orphans running resources, which is visible and recoverable, while a *surplus* one deletes them, which is neither.

**Deviation 7, outside the seven artefacts.** `--secrets-mode eso` generated a `SecretStore` + `ExternalSecret` under `base/`, ahead of the External Secrets Operator install that W6 D3 lands. Structurally identical to quirk 3, and handled the same way — the artefact is correct for the mode requested, the CRD does not exist yet. It is not adopted here; the placeholder Secret stays in `platform/secret/`, applied out-of-band, for the reason in finding 1 above.

The three checklist quirks were also audited directly against the committed artefacts, independent of what the generator emitted:

**Quirk 1 — `spec.project: default` on a generated Application. Checked, and it is the check that matters most.** Every Application here sets `spec.project: taxcalc`; `kubectl -n argocd get app -o jsonpath='{.items[*].spec.project}'` returns `taxcalc taxcalc taxcalc`. An Application that lands in `default` is completely unguarded — `default` permits `*` for source repos, destinations *and* cluster-scoped resources — so this quirk produces a green dashboard with no guardrail at all, which is strictly worse than an obvious failure.

**Quirk 2 — a missing `resources-finalizer.argocd.argoproj.io`. Audited, and deliberately resolved BOTH ways.** The standalone `taxcalc-api-dev.yaml` and the `AppProject` carry it; the ApplicationSet template deliberately does **not**, because the finalizer defeats `preserveResourcesOnDeletion: true` (finding 3 above). Treating "add the finalizer everywhere" as a blanket rule would have reintroduced the exact bug the experiment found.

**Quirk 3 — a `Rollout` CR scaffolded before Argo Rollouts is installed.** None is committed. The generator did produce one (deviation 6 above), which is the correct behaviour for `--strategy canary`; it is commented out with a `# W6 D5 lands this` note rather than deleted. Argo Rollouts is not installed on this cluster, so a live `Rollout` would be rejected by the API server for want of its CRD — and, until the generator's whitelist addition is adopted, denied by this project's `namespaceResourceWhitelist` as well. W6 D5 lands the install.

### One suggestion accepted, and one rejected

Both from the `argocd-author` run above.

**Accepted — the `Rollout` + `AnalysisTemplate` scaffold (deviation 6).** Taken as correct against the first instinct, which was to delete it because Argo Rollouts is not installed. "The CRD is missing" is an argument about *when* to apply an artefact, not about whether the artefact is right, and `--strategy canary` was the strategy asked for. Deleting it would have silently downgraded the request to a rolling update and left W6 D5 starting from nothing. Commented out with the required note, and the generator's `Rollout`/`AnalysisTemplate` entries for the AppProject whitelist are recorded with it, since the install is inert without them.

**Rejected — carrying `resources-finalizer.argocd.argoproj.io` into the ApplicationSet template (deviation 1).** The generator applied the finalizer uniformly, which is right for a standalone Application and wrong for a generated one: it silently defeats `preserveResourcesOnDeletion: true`, because that setting works precisely by withholding the finalizer. Accepting the suggestion would have converted "drop an env from the `elements:` list" from a no-op into a teardown of that environment's live resources. Rejected, and the reasoning is in the ApplicationSet's own comments so the next reader does not re-add it.

### Earlier reference-layout calls, kept for the record

**Accepted — the reference layout's `syncOptions` block, in full.** `CreateNamespace=true`, `ServerSideApply=true`, `PrunePropagationPolicy=foreground`, `PruneLast=true` and `ApplyOutOfSyncOnly=true` were taken as given rather than trimmed to the two that were obviously needed. `ServerSideApply=true` earned it immediately: the taxcalc-dev resources already existed from W5 D3's client-side `kubectl apply`, carrying `last-applied-configuration` annotations, and server-side apply is what let Argo CD adopt them cleanly with no field-manager conflict and no `--force`.

`CreateNamespace=true` was accepted for a reason that turned out to be **wrong**, and finding that out is finding 8 below — it is inert under this project, not load-bearing. It is kept because it is the correct setting the moment the project is granted the `Namespace` kind, and because removing it would make the *next* person assume namespaces are self-provisioning.

**Rejected — the reference layout's `1f1f1f1f1f1f...` image-tag placeholder.** The overlays commit a real tag (`0.2.0`) that exists in the cluster's image store. A placeholder tag resolves to `ImagePullBackOff`, which makes the Application permanently `Degraded` — and that is not merely untidy: Task 4's deliberate-failure experiment depends on being able to tell a *caused* failure apart from ambient noise, and an Application that is already Degraded for an unrelated reason destroys the signal the experiment is trying to produce. The placeholder exists in the reference so that `_bump-config.yml` has something to rewrite; a real tag serves that purpose identically, since `kustomize edit set image` replaces whatever is there.
