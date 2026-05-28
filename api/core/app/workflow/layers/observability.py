"""
Observability layer for GraphEngine.

This layer creates OpenTelemetry spans for node execution, enabling distributed
tracing of workflow execution. It establishes OTel context during node execution
so that automatic instrumentation (HTTP requests, DB queries, etc.) automatically
associates with the node span.

LLM / Knowledge Retrieval / Tool / Agent nodes are emitted via util-genai's
``ExtendedTelemetryHandler`` so they carry GenAI semantic convention attributes.
All other nodes go through the global tracer with ``node.title`` as span name.
"""

import logging
from contextvars import Token
from dataclasses import dataclass, field
from typing import Any, cast, final, override

from opentelemetry import context as context_api
from opentelemetry.trace import Span, SpanKind, Tracer, get_tracer, set_span_in_context
from opentelemetry.trace.status import Status, StatusCode

from configs import dify_config
from extensions.otel.genai_handler import get_genai_handler
from extensions.otel.parser import (
    AgentNodeOTelParser,
    DefaultNodeOTelParser,
    LLMNodeOTelParser,
    NodeOTelParser,
    RetrievalNodeOTelParser,
    ToolNodeOTelParser,
)
from extensions.otel.runtime import is_instrument_flag_enabled
from extensions.otel.semconv.node_kind import (
    is_genai_handled_kind,
    node_type_to_kind,
)
from graphon.enums import BuiltinNodeTypes, NodeType
from graphon.graph_engine.layers import GraphEngineLayer
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node

logger = logging.getLogger(__name__)

# Per-process dedup of FR-006 / FR-005 fallback warnings keyed by raw
# ``node_type`` string so a hot loop doesn't flood logs.
_LOGGED_FALLBACK_TYPES: set[str] = set()


def _log_fallback_once(node_type_repr: str, message: str) -> None:
    if node_type_repr in _LOGGED_FALLBACK_TYPES:
        return
    _LOGGED_FALLBACK_TYPES.add(node_type_repr)
    logger.warning(message)


@dataclass(slots=True)
class _NodeSpanContext:
    """State carried between ``on_node_run_start`` and ``on_node_run_end``.

    Two emit paths share this dataclass:

    - util-genai handler path: ``invocation`` and ``handler_op`` are populated;
      ``token`` is None because util-genai owns context attach/detach.
    - tracer path (default and FR-006 degraded): ``span`` and ``token`` are
      populated; ``invocation`` is None.
    """

    span: "Span"
    token: Token[context_api.Context] | None
    parser: NodeOTelParser
    kind: str
    invocation: Any | None = None
    handler_op: str = ""  # one of "llm" / "retrieval" / "execute_tool" / "invoke_agent" / ""
    extra_attrs: dict[str, str] = field(default_factory=dict)


@final
class ObservabilityLayer(GraphEngineLayer):
    """
    Layer that creates OpenTelemetry spans for node execution.

    This layer:
    - Creates a span when a node starts execution
    - Establishes OTel context so automatic instrumentation associates with the span
    - Sets complete attributes and status when node execution ends
    """

    def __init__(self) -> None:
        super().__init__()
        self._node_contexts: dict[str, _NodeSpanContext] = {}
        self._parsers: dict[NodeType, NodeOTelParser] = {}
        self._default_parser: NodeOTelParser = cast(NodeOTelParser, DefaultNodeOTelParser())
        self._is_disabled: bool = False
        self._tracer: Tracer | None = None
        self._build_parser_registry()
        self._init_tracer()

    def _init_tracer(self) -> None:
        """Initialize OpenTelemetry tracer in constructor."""
        if not (dify_config.ENABLE_OTEL or is_instrument_flag_enabled()):
            self._is_disabled = True
            return

        try:
            self._tracer = get_tracer(__name__)
        except Exception as e:
            logger.warning("Failed to get OpenTelemetry tracer: %s", e)
            self._is_disabled = True

    def _build_parser_registry(self) -> None:
        """Initialize parser registry for node types."""
        self._parsers = {
            BuiltinNodeTypes.TOOL: ToolNodeOTelParser(),
            BuiltinNodeTypes.LLM: LLMNodeOTelParser(),
            BuiltinNodeTypes.KNOWLEDGE_RETRIEVAL: RetrievalNodeOTelParser(),
            BuiltinNodeTypes.AGENT: AgentNodeOTelParser(),
        }

    def _get_parser(self, node: Node) -> NodeOTelParser:
        return self._parsers.get(node.node_type, self._default_parser)

    @override
    def on_graph_start(self) -> None:
        """Called when graph execution starts."""
        self._node_contexts.clear()

    # ------------------------------------------------------------------ start
    @override
    def on_node_run_start(self, node: Node) -> None:
        """Create a span (handler-driven for genai kinds, tracer-driven otherwise)."""
        if self._is_disabled:
            return

        try:
            if not self._tracer:
                return

            execution_id = node.execution_id
            if not execution_id:
                return

            kind = node_type_to_kind(node.node_type)
            parser = self._get_parser(node)

            # Try the util-genai handler path for the four genai-handled kinds.
            if is_genai_handled_kind(kind):
                ctx = self._start_with_handler(node=node, parser=parser, kind=kind)
                if ctx is not None:
                    self._node_contexts[execution_id] = ctx
                    return
                # Fall through to tracer path on handler unavailable / failure.

            ctx = self._start_with_tracer(node=node, parser=parser, kind=kind)
            if ctx is not None:
                self._node_contexts[execution_id] = ctx

        except Exception as e:
            logger.warning("Failed to create OpenTelemetry span for node %s: %s", node.id, e)

    def _start_with_handler(
        self, *, node: Node, parser: NodeOTelParser, kind: str
    ) -> _NodeSpanContext | None:
        """Open a util-genai-driven span for an LLM/Retrieval/Tool/Agent node."""
        handler = get_genai_handler()
        if handler is None:
            return None

        op_for_kind = {
            "llm": "llm",
            "knowledge-retrieval": "retrieval",
            "tool": "execute_tool",
            "agent": "invoke_agent",
        }
        op = op_for_kind.get(kind)
        if not op:
            return None

        try:
            invocation = parser.build_invocation(node=node, result_event=None)
            if invocation is None:
                # Parser could not build invocation (util-genai missing or
                # unsupported); fall back to tracer.
                return None
            start_method = getattr(handler, f"start_{op}")
            invocation = start_method(invocation)
            span = getattr(invocation, "span", None)
            if span is None:
                # Handler refused to attach a span; fall back so we still emit one.
                return None
        except Exception:
            _log_fallback_once(
                str(node.node_type),
                f"util-genai handler failed for kind={kind}; falling back to tracer.start_span",
            )
            return None

        return _NodeSpanContext(
            span=span,
            token=None,
            parser=parser,
            kind=kind,
            invocation=invocation,
            handler_op=op,
        )

    def _start_with_tracer(
        self, *, node: Node, parser: NodeOTelParser, kind: str
    ) -> _NodeSpanContext | None:
        """Open a span via the global TracerProvider directly."""
        if self._tracer is None:
            return None

        parent_context = context_api.get_current()
        span = self._tracer.start_span(
            node.title,
            kind=SpanKind.INTERNAL,
            context=parent_context,
        )

        new_context = set_span_in_context(span)
        token = context_api.attach(new_context)

        return _NodeSpanContext(
            span=span,
            token=token,
            parser=parser,
            kind=kind,
        )

    # ------------------------------------------------------------------ end
    @override
    def on_node_run_end(
        self, node: Node, error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        """Finish the span for ``node`` via whichever path opened it."""
        if self._is_disabled:
            return

        try:
            execution_id = node.execution_id
            if not execution_id:
                return
            node_context = self._node_contexts.get(execution_id)
            if not node_context:
                return

            try:
                if node_context.invocation is not None and node_context.handler_op:
                    self._end_with_handler(
                        node=node, error=error, result_event=result_event, ctx=node_context
                    )
                else:
                    self._end_with_tracer(
                        node=node, error=error, result_event=result_event, ctx=node_context
                    )
            finally:
                token = node_context.token
                if token is not None:
                    try:
                        context_api.detach(token)
                    except Exception:
                        logger.warning("Failed to detach OpenTelemetry token: %s", token)
                self._node_contexts.pop(execution_id, None)

        except Exception as e:
            logger.warning("Failed to end OpenTelemetry span for node %s: %s", node.id, e)

    def _end_with_handler(
        self,
        *,
        node: Node,
        error: Exception | None,
        result_event: GraphNodeEventBase | None,
        ctx: _NodeSpanContext,
    ) -> None:
        """Stop a util-genai-driven invocation; on failure degrade to tracer."""
        handler = get_genai_handler()
        invocation = ctx.invocation
        op = ctx.handler_op

        if handler is None or invocation is None:
            # Handler became unavailable mid-run — close the span ourselves.
            self._finish_span_directly(node=node, error=error, result_event=result_event, ctx=ctx)
            return

        # Re-fill invocation with values now available from result_event so the
        # helper can write the proper attribute set.
        try:
            fresh = ctx.parser.build_invocation(node=node, result_event=result_event)
            if fresh is not None:
                _copy_invocation_fields(src=fresh, dst=invocation)
        except Exception:
            logger.warning("Failed to refresh util-genai invocation for node %s", node.id, exc_info=True)

        # Common ``dify.node.*`` / ``node.*`` attrs must be written BEFORE the
        # helper ends the span — once ``stop_*`` / ``fail_*`` returns the span
        # is closed and further mutations are ignored by the SDK.
        try:
            from extensions.otel.parser.base import _set_common_node_attributes  # noqa: PLC0415

            _set_common_node_attributes(node=node, span=ctx.span, kind=ctx.kind)
        except Exception:
            logger.warning("Failed to set common node attrs on util-genai span for %s", node.id)

        try:
            if error is not None:
                from opentelemetry.util.genai.types import Error  # noqa: PLC0415

                fail_method = getattr(handler, f"fail_{op}")
                fail_method(invocation, Error(message=str(error), type=type(error)))
            else:
                stop_method = getattr(handler, f"stop_{op}")
                stop_method(invocation)
        except Exception:
            _log_fallback_once(
                str(node.node_type),
                f"util-genai stop/fail raised for op={op}; spans may be partial",
            )
            # Helper failed — close the span ourselves so we don't leak it.
            try:
                if error is not None:
                    ctx.span.record_exception(error)
                    ctx.span.set_status(Status(StatusCode.ERROR, str(error)))
                else:
                    ctx.span.set_status(Status(StatusCode.OK))
            except Exception:
                pass
            try:
                ctx.span.end()
            except Exception:
                pass

    def _end_with_tracer(
        self,
        *,
        node: Node,
        error: Exception | None,
        result_event: GraphNodeEventBase | None,
        ctx: _NodeSpanContext,
    ) -> None:
        """Finish a tracer-driven span via parser.parse(...)."""
        span = ctx.span
        try:
            ctx.parser.parse(node=node, span=span, error=error, result_event=result_event)
        finally:
            try:
                span.end()
            except Exception:
                logger.warning("Failed to end span for node %s", node.id, exc_info=True)

    def _finish_span_directly(
        self,
        *,
        node: Node,
        error: Exception | None,
        result_event: GraphNodeEventBase | None,
        ctx: _NodeSpanContext,
    ) -> None:
        """Last-ditch finish when util-genai disappears between start and end."""
        span = ctx.span
        try:
            self._default_parser.parse(node=node, span=span, error=error, result_event=result_event)
        finally:
            try:
                if error is not None:
                    span.set_status(Status(StatusCode.ERROR, str(error)))
                span.end()
            except Exception:
                pass

    @override
    def on_event(self, event) -> None:
        """Not used in this layer."""
        pass

    @override
    def on_graph_end(self, error: Exception | None) -> None:
        """Called when graph execution ends."""
        if self._node_contexts:
            logger.warning(
                "ObservabilityLayer: %d node spans were not properly ended",
                len(self._node_contexts),
            )
            self._node_contexts.clear()


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


_INVOCATION_REFRESH_FIELDS = (
    # LLMInvocation / InvokeAgentInvocation
    "request_model",
    "provider",
    "input_messages",
    "output_messages",
    "input_tokens",
    "output_tokens",
    "agent_name",
    "agent_id",
    # RetrievalInvocation
    "query",
    "documents",
    "data_source_id",
    "top_k",
    # ExecuteToolInvocation
    "tool_name",
    "tool_type",
    "tool_call_arguments",
    "tool_call_result",
    "tool_description",
    # Generic
    "attributes",
)


def _copy_invocation_fields(*, src: Any, dst: Any) -> None:
    """Copy populated fields from ``src`` invocation into ``dst``.

    util-genai's ``start_*`` already attached a span and context to ``dst``; we
    only want to update the data fields built from a fresh ``result_event``.
    """
    for fname in _INVOCATION_REFRESH_FIELDS:
        if not hasattr(src, fname) or not hasattr(dst, fname):
            continue
        value = getattr(src, fname)
        # Skip fields the parser left as a default (empty string / empty list).
        if value in ("", None):
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        try:
            setattr(dst, fname, value)
        except Exception:
            continue
