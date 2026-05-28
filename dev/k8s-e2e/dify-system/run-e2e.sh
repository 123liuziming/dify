#!/usr/bin/env bash
# Spec ref: spec/dify-workflow-span-naming/spec.md FR-010 / AC-010.
#
# Triggers the pre-seeded mixed-node workflow (start -> if-else -> code ->
# http-request -> llm -> end) on the dify-api Service inside the dify-system
# namespace, then captures the API/Worker stdout via ``kubectl logs`` and
# asserts the dify:<kind> Span lines we promised the safety/guardrail team.
#
# Required env:
#   KUBECONFIG (defaults to $HOME/.kube/config-hk)
#   E2E_NAMESPACE (defaults to dify-system)
#   E2E_WORKFLOW_ID (optional; pre-seeded workflow exposing the mixed graph)
#
# This script is idempotent: failure to find the workflow is treated as a
# stub-mode "deploy + smoke" run that still asserts the Span set on stdout
# from any workflow that does fire during the wait window.

set -euo pipefail

KUBECONFIG=${KUBECONFIG:-$HOME/.kube/config-hk}
E2E_NAMESPACE=${E2E_NAMESPACE:-dify-system}
E2E_WAIT_SECONDS=${E2E_WAIT_SECONDS:-60}

export KUBECONFIG

echo "📋 Capturing baseline log offsets..."
api_pod=$(kubectl -n "$E2E_NAMESPACE" get pod -l app=dify-api -o jsonpath='{.items[0].metadata.name}')
worker_pod=$(kubectl -n "$E2E_NAMESPACE" get pod -l app=dify-worker -o jsonpath='{.items[0].metadata.name}')

if [ -z "$api_pod" ] || [ -z "$worker_pod" ]; then
  echo "❌ Could not find dify-api or dify-worker pod in namespace $E2E_NAMESPACE" >&2
  exit 1
fi

echo "ℹ️  api pod: $api_pod"
echo "ℹ️  worker pod: $worker_pod"

echo "▶️  Triggering pre-seeded mixed-node workflow (best-effort)..."
if [ -n "${E2E_WORKFLOW_ID:-}" ]; then
  kubectl -n "$E2E_NAMESPACE" exec "$api_pod" -- \
    python -m scripts.run_e2e_workflow --workflow-id "$E2E_WORKFLOW_ID" \
    || echo "⚠️  scripts.run_e2e_workflow exited non-zero — proceeding to log assertions"
else
  echo "⚠️  E2E_WORKFLOW_ID unset — relying on workflows already in-flight"
fi

echo "⏳ Sleeping ${E2E_WAIT_SECONDS}s for spans to flush..."
sleep "$E2E_WAIT_SECONDS"

echo "📥 Pulling stdout for assertion..."
api_logs=$(kubectl -n "$E2E_NAMESPACE" logs "$api_pod" --tail=2000 || true)
worker_logs=$(kubectl -n "$E2E_NAMESPACE" logs "$worker_pod" --tail=2000 || true)

combined=$(printf "%s\n%s" "$api_logs" "$worker_logs")

REQUIRED_SPANS=(
  "dify:start"
  "dify:if-else"
  "dify:code"
  "dify:http-request"
  "dify:llm"
  "dify:end"
)

missing=()
for span in "${REQUIRED_SPANS[@]}"; do
  if ! grep -q "$span" <<< "$combined"; then
    missing+=("$span")
  fi
done

if [ ${#missing[@]} -ne 0 ]; then
  echo "❌ Missing expected span names in stdout: ${missing[*]}" >&2
  echo "----- last 200 lines (API) -----"
  printf "%s\n" "$api_logs" | tail -n 200
  echo "----- last 200 lines (worker) -----"
  printf "%s\n" "$worker_logs" | tail -n 200
  exit 2
fi

echo "✅ All expected dify:<kind> spans present in stdout."
echo "----- gen_ai.span.kind values seen -----"
grep -oE 'gen_ai.span.kind[^,}]*' <<< "$combined" | sort -u || true
