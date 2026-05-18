"""Canonical kind mapping for Dify workflow nodes.

This is the single source of truth for the BuiltinNodeTypes -> canonical kebab-case
kind mapping referenced by FR-002 of spec FEATURE-001 (AGE-42). Span name, the
``gen_ai.span.kind`` attribute for non-LLM/Retrieval/Tool/Agent nodes, and the
``dify.node.kind`` / ``dify.node.title`` / ``dify.node.kind.raw`` attribute keys
all read from this module.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-002 / FR-003 / FR-004 / FR-005
and requirements.md Appendix A.
"""

from typing import Final

from graphon.enums import BuiltinNodeTypes

# ---------------------------------------------------------------------------
# Attribute key constants
# ---------------------------------------------------------------------------

DIFY_NODE_KIND: Final[str] = "dify.node.kind"
"""Canonical kebab-case kind, equal to the suffix of the ``dify:<kind>`` span name."""

DIFY_NODE_TITLE: Final[str] = "dify.node.title"
"""Original ``node.title`` value as authored by the user; may be empty."""

DIFY_NODE_KIND_RAW: Final[str] = "dify.node.kind.raw"
"""Original ``node_type`` string, only set for the custom/unknown fallback path."""

GEN_AI_ORIGINAL_SPAN_NAME: Final[str] = "gen_ai.original.span.name"
"""Span name as written by the upstream util-genai helper before our update_name() call."""

CUSTOM_KIND: Final[str] = "custom"
"""Canonical kind for nodes whose ``node_type`` is not in the static mapping."""

DIFY_SPAN_NAME_PREFIX: Final[str] = "dify:"


# ---------------------------------------------------------------------------
# Static BuiltinNodeTypes -> canonical kind mapping (FR-002 / Appendix A)
# ---------------------------------------------------------------------------

_NODE_TYPE_TO_KIND: dict[BuiltinNodeTypes, str] = {
    BuiltinNodeTypes.START: "start",
    BuiltinNodeTypes.END: "end",
    BuiltinNodeTypes.ANSWER: "answer",
    BuiltinNodeTypes.LLM: "llm",
    BuiltinNodeTypes.KNOWLEDGE_RETRIEVAL: "knowledge-retrieval",
    BuiltinNodeTypes.TOOL: "tool",
    BuiltinNodeTypes.AGENT: "agent",
    BuiltinNodeTypes.IF_ELSE: "if-else",
    BuiltinNodeTypes.ITERATION: "iteration",
    BuiltinNodeTypes.LOOP: "loop",
    BuiltinNodeTypes.CODE: "code",
    BuiltinNodeTypes.TEMPLATE_TRANSFORM: "template-transform",
    BuiltinNodeTypes.HTTP_REQUEST: "http-request",
    BuiltinNodeTypes.LIST_OPERATOR: "list-operator",
    BuiltinNodeTypes.VARIABLE_ASSIGNER: "variable-assigner",
    BuiltinNodeTypes.LEGACY_VARIABLE_AGGREGATOR: "legacy-variable-aggregator",
    BuiltinNodeTypes.PARAMETER_EXTRACTOR: "parameter-extractor",
    BuiltinNodeTypes.QUESTION_CLASSIFIER: "question-classifier",
    BuiltinNodeTypes.DOCUMENT_EXTRACTOR: "document-extractor",
    BuiltinNodeTypes.HUMAN_INPUT: "human-input",
    BuiltinNodeTypes.DATASOURCE: "datasource",
}

# ``WORKFLOW_*`` kind strings written into ``gen_ai.span.kind`` for nodes that do
# NOT have a util-genai invocation helper (i.e. anything other than LLM /
# KNOWLEDGE_RETRIEVAL / TOOL / AGENT). Kept as a local fallback per FR-009 (4) —
# switch to ``import`` from util-genai once upstream supplies equivalents.
_KIND_TO_WORKFLOW_SPAN_KIND: dict[str, str] = {
    "start": "WORKFLOW_START",
    "end": "WORKFLOW_END",
    "answer": "WORKFLOW_ANSWER",
    "if-else": "WORKFLOW_IF_ELSE",
    "iteration": "WORKFLOW_ITERATION",
    "loop": "WORKFLOW_LOOP",
    "code": "WORKFLOW_CODE",
    "template-transform": "WORKFLOW_TEMPLATE",
    "http-request": "WORKFLOW_HTTP",
    "list-operator": "WORKFLOW_LIST",
    "variable-assigner": "WORKFLOW_VAR",
    "legacy-variable-aggregator": "WORKFLOW_VAR",
    "parameter-extractor": "WORKFLOW_PARAM_EXTRACT",
    "question-classifier": "WORKFLOW_CLASSIFY",
    "document-extractor": "WORKFLOW_DOC_EXTRACT",
    "human-input": "WORKFLOW_HUMAN_INPUT",
    "datasource": "WORKFLOW_DATASOURCE",
}

# Kinds dispatched to util-genai helpers — their ``gen_ai.span.kind`` is written
# by the helper itself, not by ``DefaultNodeOTelParser``.
_GENAI_HANDLED_KINDS: frozenset[str] = frozenset({"llm", "knowledge-retrieval", "tool", "agent"})


def node_type_to_kind(node_type: object) -> str:
    """Return canonical kebab-case kind for a node type.

    Returns :data:`CUSTOM_KIND` for any value that is not a known
    :class:`BuiltinNodeTypes`. The lookup is tolerant of plain strings so callers
    can pass ``node.node_type`` even when graphon yields a non-enum on a
    custom/unknown node.
    """
    if isinstance(node_type, BuiltinNodeTypes):
        return _NODE_TYPE_TO_KIND.get(node_type, CUSTOM_KIND)
    if isinstance(node_type, str):
        for member, kind in _NODE_TYPE_TO_KIND.items():
            if member.value == node_type or member.name == node_type:
                return kind
    return CUSTOM_KIND


def kind_to_workflow_span_kind(kind: str) -> str:
    """Return ``gen_ai.span.kind`` string for a non-genai-handled kind.

    For the four kinds dispatched to util-genai (``llm`` / ``knowledge-retrieval``
    / ``tool`` / ``agent``) this returns an empty string — the upstream helper
    writes the attribute itself. For an unknown kind (including
    :data:`CUSTOM_KIND`) the historical ``TASK`` value is returned so
    ``gen_ai.span.kind=TASK`` etqual-match queries on legacy custom nodes keep
    matching during the migration window.
    """
    if kind in _GENAI_HANDLED_KINDS:
        return ""
    return _KIND_TO_WORKFLOW_SPAN_KIND.get(kind, "TASK")


def is_genai_handled_kind(kind: str) -> bool:
    """True if a node of this kind dispatches to util-genai helpers."""
    return kind in _GENAI_HANDLED_KINDS


def span_name_for_kind(kind: str) -> str:
    """Return the ``dify:<kind>`` span name string."""
    return f"{DIFY_SPAN_NAME_PREFIX}{kind}"


__all__ = [
    "CUSTOM_KIND",
    "DIFY_NODE_KIND",
    "DIFY_NODE_KIND_RAW",
    "DIFY_NODE_TITLE",
    "DIFY_SPAN_NAME_PREFIX",
    "GEN_AI_ORIGINAL_SPAN_NAME",
    "is_genai_handled_kind",
    "kind_to_workflow_span_kind",
    "node_type_to_kind",
    "span_name_for_kind",
]
