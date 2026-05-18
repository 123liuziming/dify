"""Semantic convention shortcuts for Dify-specific spans."""

from .dify import DifySpanAttributes
from .gen_ai import ChainAttributes, GenAIAttributes, LLMAttributes, RetrieverAttributes, ToolAttributes
from .node_kind import (
    CUSTOM_KIND,
    DIFY_NODE_KIND,
    DIFY_NODE_KIND_RAW,
    DIFY_NODE_TITLE,
    DIFY_SPAN_NAME_PREFIX,
    GEN_AI_ORIGINAL_SPAN_NAME,
    is_genai_handled_kind,
    kind_to_workflow_span_kind,
    node_type_to_kind,
    span_name_for_kind,
)

__all__ = [
    "CUSTOM_KIND",
    "DIFY_NODE_KIND",
    "DIFY_NODE_KIND_RAW",
    "DIFY_NODE_TITLE",
    "DIFY_SPAN_NAME_PREFIX",
    "GEN_AI_ORIGINAL_SPAN_NAME",
    "ChainAttributes",
    "DifySpanAttributes",
    "GenAIAttributes",
    "LLMAttributes",
    "RetrieverAttributes",
    "ToolAttributes",
    "is_genai_handled_kind",
    "kind_to_workflow_span_kind",
    "node_type_to_kind",
    "span_name_for_kind",
]
