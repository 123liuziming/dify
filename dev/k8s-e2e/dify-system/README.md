# Dify E2E – OTel console exporter (FEATURE-001 / AGE-42)

Spec ref: `spec/dify-workflow-span-naming/spec.md` FR-010 / AC-010.

This directory provides the manifests + driver for a stdout-only end-to-end
verification of the Dify workflow span naming change. It deploys API + Worker
into the pre-existing `dify-system` namespace on the ACK cluster pointed to by
`~/.kube/config-hk`, configures the OTel console exporter, and grep-asserts
the expected `dify:<kind>` Span names off `kubectl logs`.

## Usage

```bash
# Build + push (or load) the Dify image at tag dify-api:e2e-age-42 first.
KUBECONFIG=~/.kube/config-hk make e2e-dify-otel-console

# Tear down
KUBECONFIG=~/.kube/config-hk make e2e-dify-otel-console-clean
```

## What `make e2e-dify-otel-console` does

1. `kubectl apply -k dev/k8s-e2e/dify-system` (idempotent).
2. Waits for `dify-api` / `dify-worker` rollout (`--timeout=300s`).
3. Runs `dev/k8s-e2e/dify-system/run-e2e.sh` which:
   - Locates the API + Worker pods.
   - Triggers the pre-seeded mixed-node workflow if `E2E_WORKFLOW_ID` is set.
   - Sleeps `E2E_WAIT_SECONDS` (default 60) so spans flush.
   - Asserts that `dify:start` / `dify:if-else` / `dify:code` /
     `dify:http-request` / `dify:llm` / `dify:end` all appear in stdout.

Exit codes:

- `0` — all required Span names found.
- `1` — one or both pods could not be located.
- `2` — one or more Span names missing from stdout.

## Image channel

The deployment manifests reference `dify-api:e2e-age-42`. Override via
`kubectl set image` after `apply -k`, or substitute upstream via a kustomize
overlay. The current default is intentionally a placeholder so reviewers must
make an explicit choice between:

- `registry.cn-hongkong.aliyuncs.com/<your-org>/dify-api:age-42` (push-based)
- local `kind` / `minikube` image load (skip-push)

The implementation PR description must record the choice.

## Limitations / TODOs

- The script's "trigger pre-seeded workflow" path expects a `scripts.run_e2e_workflow`
  helper that is not yet shipped in this PR. Provide a real workflow id via
  `E2E_WORKFLOW_ID` and a dispatcher that targets the dify-api Service when
  available, or invoke a curl-based trigger before the sleep.
- DB / Redis / vector store services are assumed to already exist in the
  `dify-system` namespace under the names `postgres-svc` / `redis-svc`.
  Override via the ConfigMap if your namespace uses different names.
- LLM nodes use whatever provider Dify is configured with; for offline runs
  set `gen_ai.request.model="mock"` or back-fill `LLMInvocation` fields.
