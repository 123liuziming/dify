"""
Base parser interface and utilities for OpenTelemetry node parsers.

Content gating: ``should_include_content()`` controls whether content-bearing
span attributes (inputs, outputs, prompts, completions, documents) are written.
Gate is only active in EE (``ENTERPRISE_ENABLED=True``) when
``ENTERPRISE_INCLUDE_CONTENT=False``; CE behaviour is unchanged.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-001/FR-002/FR-003/FR-004/FR-005
(node-kind-based span name + ``gen_ai.span.kind`` finer granularity + new
``dify.node.*`` attributes + custom-node fallback).
"""

import json
from typing import Any, Protocol

from opentelemetry.trace import Span
from opentelemetry.trace.status import Status, StatusCode
from pydantic import BaseModel

from configs import dify_config
from extensions.otel.semconv.gen_ai import ChainAttributes, GenAIAttributes
from extensions.otel.semconv.node_kind import (
    CUSTOM_KIND,
    DIFY_NODE_KIND,
    DIFY_NODE_KIND_RAW,
    DIFY_NODE_TITLE,
    is_genai_handled_kind,
    kind_to_workflow_span_kind,
    node_type_to_kind,
)
from graphon.file import File
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node
from graphon.variables import Segment


def should_include_content() -> bool:
    """Return True if content should be written to spans.

    CE (ENTERPRISE_ENABLED=False): always True — no behaviour change.
    """
    if not dify_config.ENTERPRISE_ENABLED:
        return True
    return dify_config.ENTERPRISE_INCLUDE_CONTENT


def safe_json_dumps(obj: Any, ensure_ascii: bool = False) -> str:
    """
    Safely serialize objects to JSON, handling non-serializable types.

    Handles:
    - Segment types (ArrayFileSegment, FileSegment, etc.) - converts to their value
    - File objects - converts to dict using to_dict()
    - BaseModel objects - converts using model_dump()
    - Other types - falls back to str() representation

    Args:
        obj: Object to serialize
        ensure_ascii: Whether to ensure ASCII encoding

    Returns:
        JSON string representation of the object
    """

    def _convert_value(value: Any) -> Any:
        """Recursively convert non-serializable values."""
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Segment):
            # Convert Segment to its underlying value
            return _convert_value(value.value)
        if isinstance(value, File):
            # Convert File to dict
            return value.to_dict()
        if isinstance(value, BaseModel):
            # Convert Pydantic model to dict
            return _convert_value(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {k: _convert_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_convert_value(item) for item in value]
        # Fallback to string representation for unknown types
        return str(value)

    try:
        converted = _convert_value(obj)
        return json.dumps(converted, ensure_ascii=ensure_ascii)
    except (TypeError, ValueError) as e:
        # If conversion still fails, return error message as string
        return json.dumps(
            {"error": f"Failed to serialize: {type(obj).__name__}", "message": str(e)}, ensure_ascii=ensure_ascii
        )


class NodeOTelParser(Protocol):
    """Parser interface for node-specific OpenTelemetry enrichment.

    Two paths are supported, chosen by the caller (``ObservabilityLayer``):

    - ``build_invocation`` — for nodes that dispatch to util-genai
      (LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT). Returns the invocation
      dataclass instance that the handler will start/stop. May return ``None``
      to signal "no invocation, fall back to tracer".
    - ``parse`` — for nodes that go through the global tracer directly. Writes
      span attributes after the span has been opened.
    """

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None: ...

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        """Construct an invocation dataclass for util-genai start/stop calls.

        The default implementation returns ``None``, indicating that this parser
        has no util-genai counterpart and the caller should use the tracer path.
        """
        ...


def _set_common_node_attributes(*, node: Node, span: "Span", kind: str) -> None:
    """Write common ``dify.node.*`` / ``node.*`` / ``gen_ai.framework`` attrs.

    Called by both the tracer path and the util-genai-driven paths so the same
    set of node-identification attributes lands on every span (FR-004).
    """
    span.set_attribute("node.id", node.id)
    if node.execution_id:
        span.set_attribute("node.execution_id", node.execution_id)
    # ``node.type`` historically held the BuiltinNodeTypes string. We now write
    # the canonical kind so legacy equality queries on common kinds keep working.
    span.set_attribute("node.type", kind)
    span.set_attribute(DIFY_NODE_KIND, kind)
    span.set_attribute(DIFY_NODE_TITLE, node.title or "")

    span.set_attribute(GenAIAttributes.FRAMEWORK, "dify")


class DefaultNodeOTelParser:
    """Fallback parser used for non-genai-handled workflow nodes.

    For LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT, ``ObservabilityLayer`` routes
    to the dedicated parser's ``build_invocation`` so this class is reached only
    when the util-genai handler is unavailable (FR-006 degraded path) or when
    the kind is one of the workflow-control / workflow-tool kinds (FR-003).
    """

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        kind = node_type_to_kind(node.node_type)
        _set_common_node_attributes(node=node, span=span, kind=kind)

        # Custom / unknown node bookkeeping (FR-005).
        if kind == CUSTOM_KIND:
            try:
                raw = getattr(node.node_type, "value", node.node_type)
                if raw:
                    span.set_attribute(DIFY_NODE_KIND_RAW, str(raw))
            except Exception:
                pass

        # ``gen_ai.span.kind``: WORKFLOW_* for non-genai kinds, TASK for custom.
        # For genai-handled kinds we never reach this branch when the handler
        # path succeeded; on the FR-006 degraded fallback the caller writes the
        # appropriate ``LLM`` / ``RETRIEVER`` / ``TOOL`` / ``TASK`` value first
        # and we leave it untouched.
        if not is_genai_handled_kind(kind):
            span.set_attribute(GenAIAttributes.SPAN_KIND, kind_to_workflow_span_kind(kind))

        # Extract inputs and outputs from result_event
        if result_event and result_event.node_run_result:
            node_run_result = result_event.node_run_result
            if should_include_content():
                if node_run_result.inputs:
                    span.set_attribute(ChainAttributes.INPUT_VALUE, safe_json_dumps(node_run_result.inputs))
                if node_run_result.outputs:
                    span.set_attribute(ChainAttributes.OUTPUT_VALUE, safe_json_dumps(node_run_result.outputs))

        if error:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)))
        else:
            span.set_status(Status(StatusCode.OK))

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        """Default parser does not produce a util-genai invocation."""
        return None
