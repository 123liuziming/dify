"""
Parser for AGENT nodes — emits Spans through util-genai ``invoke_agent`` helper.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-009 (2) and Appendix C.
``invoke_agent`` writes ``gen_ai.span.kind=INVOKE_AGENT`` (note: this differs
from the historical ``TASK`` value, see FR-008 migration guidance).
"""

from typing import Any

from opentelemetry.trace import Span

from extensions.otel.parser.base import _set_common_node_attributes
from extensions.otel.semconv.gen_ai import GenAIAttributes
from extensions.otel.semconv.node_kind import node_type_to_kind
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node


class AgentNodeOTelParser:
    """Parser for AGENT workflow nodes."""

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        try:
            from opentelemetry.util.genai.extended_types import InvokeAgentInvocation  # noqa: PLC0415
        except ImportError:
            return None

        invocation = InvokeAgentInvocation(agent_name=node.title or "")
        invocation.agent_id = node.id

        if not result_event or not result_event.node_run_result:
            return invocation

        node_run_result = result_event.node_run_result
        process_data = node_run_result.process_data or {}

        # Best-effort: pull request_model from process_data if the underlying
        # implementation surfaces it (Dify Agent nodes tend to surface
        # ``model_name`` similar to LLM nodes). Empty string is acceptable.
        request_model = process_data.get("model_name") or process_data.get("model") or ""
        if request_model:
            invocation.request_model = request_model

        # ``input_messages`` / ``output_messages`` left empty until upstream
        # Dify Agent emits them in a standardised shape (FR-009 / Appendix C).
        return invocation

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        """FR-006 degraded path: emit common attrs and historical TASK kind."""
        kind = node_type_to_kind(node.node_type)
        _set_common_node_attributes(node=node, span=span, kind=kind)
        span.set_attribute(GenAIAttributes.SPAN_KIND, "TASK")
