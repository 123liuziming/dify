"""
Parser for tool nodes that captures tool-specific metadata.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-009 (2) — tool spans are
now emitted via ``handler.start_execute_tool`` / ``stop_execute_tool`` driven
by ``build_invocation``. ``parse`` is kept for the FR-006 degraded path.
"""

from typing import Any

from opentelemetry.trace import Span

from extensions.otel.parser.base import (
    DefaultNodeOTelParser,
    _set_common_node_attributes,
    safe_json_dumps,
    should_include_content,
)
from extensions.otel.semconv.gen_ai import GenAIAttributes, ToolAttributes
from extensions.otel.semconv.node_kind import node_type_to_kind
from graphon.enums import WorkflowNodeExecutionMetadataKey
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node
from graphon.nodes.tool.entities import ToolNodeData


class ToolNodeOTelParser:
    """Parser for tool nodes that captures tool-specific metadata."""

    def __init__(self) -> None:
        self._delegate = DefaultNodeOTelParser()

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        try:
            from opentelemetry.util.genai.extended_types import ExecuteToolInvocation  # noqa: PLC0415
        except ImportError:
            return None

        tool_data = getattr(node, "_node_data", None)

        invocation = ExecuteToolInvocation(tool_name=node.title or "")
        if isinstance(tool_data, ToolNodeData):
            invocation.tool_name = node.title or ""
            invocation.tool_type = tool_data.provider_type.value

        if not result_event or not result_event.node_run_result:
            return invocation

        node_run_result = result_event.node_run_result

        if node_run_result.metadata:
            tool_info = node_run_result.metadata.get(WorkflowNodeExecutionMetadataKey.TOOL_INFO, {})
            if tool_info:
                invocation.tool_description = safe_json_dumps(tool_info)

        if should_include_content():
            if node_run_result.inputs:
                invocation.tool_call_arguments = safe_json_dumps(node_run_result.inputs)
            if node_run_result.outputs:
                invocation.tool_call_result = safe_json_dumps(node_run_result.outputs)
        return invocation

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        """FR-006 degraded path: write tool attrs directly to a tracer-owned span."""
        kind = node_type_to_kind(node.node_type)
        _set_common_node_attributes(node=node, span=span, kind=kind)
        span.set_attribute(GenAIAttributes.SPAN_KIND, "TOOL")

        tool_data = getattr(node, "_node_data", None)
        if not isinstance(tool_data, ToolNodeData):
            return

        span.set_attribute(ToolAttributes.TOOL_NAME, node.title)
        span.set_attribute(ToolAttributes.TOOL_TYPE, tool_data.provider_type.value)

        # Extract tool info from metadata (consistent with aliyun_trace)
        tool_info = {}
        if result_event and result_event.node_run_result:
            node_run_result = result_event.node_run_result
            if node_run_result.metadata:
                tool_info = node_run_result.metadata.get(WorkflowNodeExecutionMetadataKey.TOOL_INFO, {})

        if tool_info:
            span.set_attribute(ToolAttributes.TOOL_DESCRIPTION, safe_json_dumps(tool_info))

        if not should_include_content():
            return

        if result_event and result_event.node_run_result and result_event.node_run_result.inputs:
            span.set_attribute(ToolAttributes.TOOL_CALL_ARGUMENTS, safe_json_dumps(result_event.node_run_result.inputs))

        if result_event and result_event.node_run_result and result_event.node_run_result.outputs:
            span.set_attribute(ToolAttributes.TOOL_CALL_RESULT, safe_json_dumps(result_event.node_run_result.outputs))
