"""
Parser for LLM nodes that captures LLM-specific metadata.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-009 (2) — LLM spans are now
emitted via ``handler.start_llm`` / ``stop_llm``; ``build_invocation`` constructs
the :class:`LLMInvocation` dataclass that the handler manages. ``parse`` is kept
for the FR-006 degraded path (handler unavailable or raised).
"""

import logging
from collections.abc import Mapping
from typing import Any

from opentelemetry.trace import Span

from extensions.otel.parser.base import (
    DefaultNodeOTelParser,
    _set_common_node_attributes,
    safe_json_dumps,
    should_include_content,
)
from extensions.otel.semconv.gen_ai import GenAIAttributes, LLMAttributes
from extensions.otel.semconv.node_kind import node_type_to_kind
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node

logger = logging.getLogger(__name__)


def _format_input_messages(process_data: Mapping[str, Any]) -> str:
    """
    Format input messages from process_data for LLM spans.

    Args:
        process_data: Process data containing prompts

    Returns:
        JSON string of formatted input messages
    """
    try:
        if not isinstance(process_data, dict):
            return safe_json_dumps([])

        prompts = process_data.get("prompts", [])
        if not prompts:
            return safe_json_dumps([])

        valid_roles = {"system", "user", "assistant", "tool"}
        input_messages = []
        for prompt in prompts:
            if not isinstance(prompt, dict):
                continue

            role = prompt.get("role", "")
            text = prompt.get("text", "")

            if not role or role not in valid_roles:
                continue

            if text:
                message = {"role": role, "parts": [{"type": "text", "content": text}]}
                input_messages.append(message)

        return safe_json_dumps(input_messages)
    except Exception as e:
        logger.warning("Failed to format input messages: %s", e, exc_info=True)
        return safe_json_dumps([])


def _format_output_messages(outputs: Mapping[str, Any]) -> str:
    """
    Format output messages from outputs for LLM spans.

    Args:
        outputs: Output data containing text and finish_reason

    Returns:
        JSON string of formatted output messages
    """
    try:
        if not isinstance(outputs, dict):
            return safe_json_dumps([])

        text = outputs.get("text", "")
        finish_reason = outputs.get("finish_reason", "")

        if not text:
            return safe_json_dumps([])

        valid_finish_reasons = {"stop", "length", "content_filter", "tool_call", "error"}
        if finish_reason not in valid_finish_reasons:
            finish_reason = "stop"

        output_message = {
            "role": "assistant",
            "parts": [{"type": "text", "content": text}],
            "finish_reason": finish_reason,
        }

        return safe_json_dumps([output_message])
    except Exception as e:
        logger.warning("Failed to format output messages: %s", e, exc_info=True)
        return safe_json_dumps([])


def _build_invocation_messages(
    process_data: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> tuple[list, list]:
    """Convert prompts/outputs into util-genai ``InputMessage`` / ``OutputMessage``.

    Returns ``([], [])`` when util-genai is not importable so callers can keep
    going on the degraded path (FR-006).
    """
    try:
        # Local import: util-genai is the runtime emitter only when available.
        from opentelemetry.util.genai.types import InputMessage, OutputMessage, Text  # noqa: PLC0415
    except ImportError:
        return [], []

    valid_roles = {"system", "user", "assistant", "tool"}
    valid_finish_reasons = {"stop", "length", "content_filter", "tool_call", "error"}

    input_messages: list = []
    if isinstance(process_data, dict):
        for prompt in process_data.get("prompts", []) or []:
            if not isinstance(prompt, dict):
                continue
            role = prompt.get("role", "")
            text = prompt.get("text", "")
            if not role or role not in valid_roles or not text:
                continue
            input_messages.append(InputMessage(role=role, parts=[Text(content=text)]))

    output_messages: list = []
    if isinstance(outputs, dict):
        text = outputs.get("text", "") or ""
        if text:
            finish_reason = outputs.get("finish_reason") or "stop"
            if finish_reason not in valid_finish_reasons:
                finish_reason = "stop"
            output_messages.append(
                OutputMessage(role="assistant", parts=[Text(content=text)], finish_reason=finish_reason)
            )

    return input_messages, output_messages


class LLMNodeOTelParser:
    """Parser for LLM nodes that captures LLM-specific metadata."""

    def __init__(self) -> None:
        self._delegate = DefaultNodeOTelParser()

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        """Build a :class:`LLMInvocation` for ``handler.start_llm``.

        Returns ``None`` if util-genai is not importable or ``result_event`` is
        empty (no point starting an invocation we cannot fill in).
        """
        try:
            from opentelemetry.util.genai.types import LLMInvocation  # noqa: PLC0415
        except ImportError:
            return None

        if not result_event or not result_event.node_run_result:
            return LLMInvocation(request_model="")

        node_run_result = result_event.node_run_result
        process_data: Mapping[str, Any] = node_run_result.process_data or {}
        outputs: Mapping[str, Any] = node_run_result.outputs or {}
        usage_data = process_data.get("usage") or outputs.get("usage") or {}

        request_model = process_data.get("model_name") or ""
        provider = process_data.get("model_provider") or ""

        input_messages: list = []
        output_messages: list = []
        if should_include_content():
            input_messages, output_messages = _build_invocation_messages(process_data, outputs)

        invocation = LLMInvocation(
            request_model=request_model,
            provider=provider,
            input_messages=input_messages,
            output_messages=output_messages,
        )

        # Token usage is metadata, not content — write regardless of content gate.
        if usage_data:
            invocation.input_tokens = int(usage_data.get("prompt_tokens", 0) or 0)
            invocation.output_tokens = int(usage_data.get("completion_tokens", 0) or 0)
        return invocation

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        """FR-006 degraded path: write LLM attrs directly to a tracer-owned span."""
        # Common attrs first (writes ``dify.node.kind`` etc).
        kind = node_type_to_kind(node.node_type)
        _set_common_node_attributes(node=node, span=span, kind=kind)
        # On the degraded path we restore the historical ``LLM`` value.
        span.set_attribute(GenAIAttributes.SPAN_KIND, "LLM")

        if not result_event or not result_event.node_run_result:
            return

        node_run_result = result_event.node_run_result
        process_data = node_run_result.process_data or {}
        outputs = node_run_result.outputs or {}

        # Extract usage data (from process_data or outputs)
        usage_data = process_data.get("usage") or outputs.get("usage") or {}

        # Model and provider information
        model_name = process_data.get("model_name") or ""
        model_provider = process_data.get("model_provider") or ""

        if model_name:
            span.set_attribute(LLMAttributes.REQUEST_MODEL, model_name)
        if model_provider:
            span.set_attribute(LLMAttributes.PROVIDER_NAME, model_provider)

        # Token usage
        if usage_data:
            prompt_tokens = usage_data.get("prompt_tokens", 0)
            completion_tokens = usage_data.get("completion_tokens", 0)
            total_tokens = usage_data.get("total_tokens", 0)

            span.set_attribute(LLMAttributes.USAGE_INPUT_TOKENS, prompt_tokens)
            span.set_attribute(LLMAttributes.USAGE_OUTPUT_TOKENS, completion_tokens)
            span.set_attribute(LLMAttributes.USAGE_TOTAL_TOKENS, total_tokens)

        if not should_include_content():
            return

        # Prompts and completion
        prompts = process_data.get("prompts", [])
        if prompts:
            prompts_json = safe_json_dumps(prompts)
            span.set_attribute(LLMAttributes.PROMPT, prompts_json)

        text_output = str(outputs.get("text", ""))
        if text_output:
            span.set_attribute(LLMAttributes.COMPLETION, text_output)

        # Finish reason
        finish_reason = outputs.get("finish_reason") or ""
        if finish_reason:
            span.set_attribute(LLMAttributes.RESPONSE_FINISH_REASON, finish_reason)

        # Structured input/output messages
        gen_ai_input_message = _format_input_messages(process_data)
        gen_ai_output_message = _format_output_messages(outputs)

        span.set_attribute(LLMAttributes.INPUT_MESSAGE, gen_ai_input_message)
        span.set_attribute(LLMAttributes.OUTPUT_MESSAGE, gen_ai_output_message)
