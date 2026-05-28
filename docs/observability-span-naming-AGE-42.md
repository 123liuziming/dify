# Dify Workflow Span Naming Migration (AGE-42 / FEATURE-001)

> Status: Active migration window
> Spec: `spec/dify-workflow-span-naming/spec.md`
> Source issue: AGE-42 "Dify工作流Span埋点优化"

This change rewrites the Dify workflow node OTel span name and the
`gen_ai.span.kind` semantic to give downstream consumers (ARMS / Grafana /
Guardrail rules) a stable, kind-aware identifier for every node.

## What changed

1. **Span name**: was `<node.title>` (often a user-typed Chinese label or
   "代码" / "分支"), now always `dify:<canonical-kind>` (ASCII kebab-case).
2. **`gen_ai.span.kind`**: previously collapsed to `TASK` for any node other
   than `LLM` / `KNOWLEDGE_RETRIEVAL` / `TOOL`. Now:
   - LLM / Retrieval / Tool / Agent: written by util-genai →
     `LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`.
   - Workflow control / utility nodes: written by `DefaultNodeOTelParser` →
     `WORKFLOW_*` (see canonical kind table below).
   - Custom / unknown nodes: `TASK` (unchanged).
3. **New attributes** on every node span:
   - `dify.node.kind` — canonical kebab-case kind.
   - `dify.node.title` — original user-authored title.
   - `dify.node.kind.raw` — only on the custom-fallback path, holds the raw
     `node_type` string.
   - `gen_ai.original.span.name` — only on util-genai-emitted spans, holds the
     name util-genai wrote before our `update_name(...)` call.
4. **`node.type`** now stores the canonical kind (e.g. `if-else`) so legacy
   equality queries on common kinds keep working.
5. **Dependency**: `loongsuite-util-genai` is added to `api/pyproject.toml`.
   The extended handler's `start_*` / `stop_*` / `fail_*` lifecycle is now the
   primary emitter for LLM / Retrieval / Tool / Agent spans. If the package is
   not installed (or its handler raises), `ObservabilityLayer` falls back to
   the legacy `tracer.start_span` path with the historical
   `gen_ai.span.kind` values; the new attribute set is still written.
6. **No runtime feature flag**: the new span name and attribute set are always
   on. The legacy `gen_ai.span.kind=TASK`-equality consumers must perform a
   one-shot migration to set-membership matching as described below.

## Canonical kind reference

| BuiltinNodeTypes              | `dify.node.kind`              | span name                          | `gen_ai.span.kind`     |
| ----------------------------- | ----------------------------- | ---------------------------------- | ---------------------- |
| `START`                       | `start`                       | `dify:start`                       | `WORKFLOW_START`       |
| `END`                         | `end`                         | `dify:end`                         | `WORKFLOW_END`         |
| `ANSWER`                      | `answer`                      | `dify:answer`                      | `WORKFLOW_ANSWER`      |
| `LLM`                         | `llm`                         | `dify:llm`                         | `LLM` (util-genai)     |
| `KNOWLEDGE_RETRIEVAL`         | `knowledge-retrieval`         | `dify:knowledge-retrieval`         | `RETRIEVER` (util-genai) |
| `TOOL`                        | `tool`                        | `dify:tool`                        | `EXECUTE_TOOL` (util-genai) |
| `AGENT`                       | `agent`                       | `dify:agent`                       | `INVOKE_AGENT` (util-genai) |
| `IF_ELSE`                     | `if-else`                     | `dify:if-else`                     | `WORKFLOW_IF_ELSE`     |
| `ITERATION`                   | `iteration`                   | `dify:iteration`                   | `WORKFLOW_ITERATION`   |
| `LOOP`                        | `loop`                        | `dify:loop`                        | `WORKFLOW_LOOP`        |
| `CODE`                        | `code`                        | `dify:code`                        | `WORKFLOW_CODE`        |
| `TEMPLATE_TRANSFORM`          | `template-transform`          | `dify:template-transform`          | `WORKFLOW_TEMPLATE`    |
| `HTTP_REQUEST`                | `http-request`                | `dify:http-request`                | `WORKFLOW_HTTP`        |
| `LIST_OPERATOR`               | `list-operator`               | `dify:list-operator`               | `WORKFLOW_LIST`        |
| `VARIABLE_ASSIGNER`           | `variable-assigner`           | `dify:variable-assigner`           | `WORKFLOW_VAR`         |
| `LEGACY_VARIABLE_AGGREGATOR`  | `legacy-variable-aggregator`  | `dify:legacy-variable-aggregator`  | `WORKFLOW_VAR`         |
| `PARAMETER_EXTRACTOR`         | `parameter-extractor`         | `dify:parameter-extractor`         | `WORKFLOW_PARAM_EXTRACT` |
| `QUESTION_CLASSIFIER`         | `question-classifier`         | `dify:question-classifier`         | `WORKFLOW_CLASSIFY`    |
| `DOCUMENT_EXTRACTOR`          | `document-extractor`          | `dify:document-extractor`          | `WORKFLOW_DOC_EXTRACT` |
| `HUMAN_INPUT`                 | `human-input`                 | `dify:human-input`                 | `WORKFLOW_HUMAN_INPUT` |
| `DATASOURCE`                  | `datasource`                  | `dify:datasource`                  | `WORKFLOW_DATASOURCE`  |
| (custom / unknown)            | `custom`                      | `dify:custom`                      | `TASK`                 |

> The util-genai-written values are subject to upstream change. Implementation
> teams must verify the exact strings during PR review and update this table
> if util-genai writes something different.

## Downstream migration guide

### One-shot rule rewrite

Any rule using `gen_ai.span.kind = "TASK"` to catch "non-LLM/Retriever/Tool"
nodes must change to a set-membership match, e.g.:

```
gen_ai.span.kind in (
  "WORKFLOW_START", "WORKFLOW_END", "WORKFLOW_ANSWER",
  "WORKFLOW_IF_ELSE", "WORKFLOW_ITERATION", "WORKFLOW_LOOP",
  "WORKFLOW_CODE", "WORKFLOW_TEMPLATE", "WORKFLOW_HTTP",
  "WORKFLOW_LIST", "WORKFLOW_VAR", "WORKFLOW_PARAM_EXTRACT",
  "WORKFLOW_CLASSIFY", "WORKFLOW_DOC_EXTRACT", "WORKFLOW_HUMAN_INPUT",
  "WORKFLOW_DATASOURCE", "INVOKE_AGENT", "TASK"
)
```

For the AGENT node specifically, the value moved from `TASK` to
`INVOKE_AGENT` — guardrail rules that care about agent invocations should add
`INVOKE_AGENT` explicitly.

For TOOL nodes, the value moved from `TOOL` to `EXECUTE_TOOL`. Add both
during the cutover window if your dashboards span data on either side of the
deploy.

### Stable equality keys (no migration needed)

These keys did not change and remain reliable for filtering:

- `gen_ai.framework = "dify"`
- `node.type` (now holds canonical kind such as `if-else`)
- `dify.node.kind` (new, equivalent to `node.type`)

### `aliyun_trace` provider

The server-side rebuild path in `api/providers/trace/trace-aliyun/...` is
**not** updated in this PR. Until it is (tracked as Future Work FW-1 in the
spec), dashboards that subscribe to both the realtime OTel path and the
`aliyun_trace` rebuild path will see naming drift. Prefer the realtime path
for new alerts during this transition.

## util-genai environment variables

Set on the API + Worker process (see
`dev/k8s-e2e/dify-system/configmap-otel-console.yaml` for an example):

- `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT|SPAN_ONLY|EVENT_ONLY|SPAN_AND_EVENT`

Dify's own `should_include_content()` gate continues to apply on top: when
content is gated off in EE mode, prompt / output / messages / documents /
tool arguments / tool results are not written to invocations.

## E2E verification (FR-010)

Run `make e2e-dify-otel-console` against `~/.kube/config-hk` to deploy a
console-exporter Dify into `dify-system` and assert that the expected
`dify:<kind>` span lines reach stdout. See
`dev/k8s-e2e/dify-system/README.md` for the manifest layout.

## Rollback

There is no runtime rollback. To revert the naming change you must redeploy
the previous Dify image. The new attributes (`dify.node.kind`,
`dify.node.title`) are additive and do not break existing queries.
