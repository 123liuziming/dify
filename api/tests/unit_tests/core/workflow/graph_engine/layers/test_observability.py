"""
Tests for ObservabilityLayer.

Test coverage:
- Initialization and enable/disable logic
- Node span lifecycle (start, end, error handling)
- Parser integration (default, tool, LLM, retrieval, agent parsers)
- Result event parameter extraction (inputs/outputs)
- Graph lifecycle management
- Disabled mode behavior
- Spec FEATURE-001 (AGE-42): canonical span names ``dify:<kind>``,
  ``dify.node.kind`` / ``dify.node.title`` / ``node.type``,
  ``gen_ai.span.kind`` ``WORKFLOW_*`` for non-genai-handled kinds, custom
  fallback to ``dify:custom``, util-genai handler degraded path.
"""

from unittest.mock import patch

import pytest
from opentelemetry.trace import StatusCode

from core.app.workflow.layers.observability import ObservabilityLayer
from extensions.otel.semconv.node_kind import (
    DIFY_NODE_KIND,
    DIFY_NODE_KIND_RAW,
    DIFY_NODE_TITLE,
    is_genai_handled_kind,
    kind_to_workflow_span_kind,
    node_type_to_kind,
    span_name_for_kind,
)
from graphon.enums import BuiltinNodeTypes


class TestObservabilityLayerInitialization:
    """Test ObservabilityLayer initialization logic."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false")
    def test_initialization_when_otel_enabled(self, tracer_provider_with_memory_exporter):
        """Test that layer initializes correctly when OTel is enabled."""
        layer = ObservabilityLayer()
        assert not layer._is_disabled
        assert layer._tracer is not None
        assert BuiltinNodeTypes.TOOL in layer._parsers
        assert BuiltinNodeTypes.AGENT in layer._parsers
        assert layer._default_parser is not None

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", False)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_true")
    def test_initialization_when_instrument_flag_enabled(self, tracer_provider_with_memory_exporter):
        """Test that layer enables when instrument flag is enabled."""
        layer = ObservabilityLayer()
        assert not layer._is_disabled
        assert layer._tracer is not None
        assert BuiltinNodeTypes.TOOL in layer._parsers
        assert layer._default_parser is not None


class TestObservabilityLayerNodeSpanLifecycle:
    """Test node span creation and lifecycle management."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_node_span_created_and_ended(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node
    ):
        """FR-001: span name is ``dify:<canonical-kind>`` regardless of node.title."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_llm_node)
        layer.on_node_run_end(mock_llm_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == span_name_for_kind("llm")
        assert spans[0].status.status_code == StatusCode.OK

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_node_error_recorded_in_span(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node
    ):
        """Test that node execution errors are recorded in span."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        error = ValueError("Test error")
        layer.on_node_run_start(mock_llm_node)
        layer.on_node_run_end(mock_llm_node, error)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR
        assert len(spans[0].events) > 0
        assert any("exception" in event.name.lower() for event in spans[0].events)

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_node_end_without_start_handled_gracefully(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node
    ):
        """Test that ending a node without start doesn't crash."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_end(mock_llm_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 0


class TestObservabilityLayerSpecFeature001:
    """FR-001 / FR-002 / FR-003 / FR-004 / FR-005 of FEATURE-001."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    @pytest.mark.parametrize("node_type", list(BuiltinNodeTypes))
    def test_span_name_and_attrs_for_every_builtin_node_type(
        self,
        tracer_provider_with_memory_exporter,
        memory_span_exporter,
        node_factory,
        node_type,
    ):
        """All 21 BuiltinNodeTypes produce ``dify:<kind>`` span name and ``dify.node.*`` attrs."""
        node = node_factory(node_type)
        kind = node_type_to_kind(node_type)

        layer = ObservabilityLayer()
        layer.on_graph_start()
        layer.on_node_run_start(node)
        layer.on_node_run_end(node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == span_name_for_kind(kind)
        attrs = span.attributes
        assert attrs[DIFY_NODE_KIND] == kind
        # ``dify.node.title`` always written, may be empty string but key must exist
        assert DIFY_NODE_TITLE in attrs
        # ``node.type`` mirrors canonical kind (FR-004)
        assert attrs["node.type"] == kind
        # ``gen_ai.framework`` preserved
        assert attrs["gen_ai.framework"] == "dify"
        # On the FR-006 degraded path the genai-handled kinds get the historical
        # ``LLM`` / ``RETRIEVER`` / ``TOOL`` / ``TASK`` value; the workflow kinds
        # get ``WORKFLOW_*``. Either is acceptable for this assertion provided
        # we are not collapsing every non-LLM/Retriever node into ``TASK``.
        kind_attr = attrs.get("gen_ai.span.kind")
        if is_genai_handled_kind(kind):
            assert kind_attr in {"LLM", "RETRIEVER", "TOOL", "TASK"}
        else:
            assert kind_attr == kind_to_workflow_span_kind(kind)
            assert kind_attr.startswith("WORKFLOW_")

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_custom_node_falls_back_to_dify_custom(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_custom_node
    ):
        """FR-005: unknown ``node_type`` produces ``dify:custom`` and ``dify.node.kind.raw``."""
        layer = ObservabilityLayer()
        layer.on_graph_start()
        layer.on_node_run_start(mock_custom_node)
        layer.on_node_run_end(mock_custom_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert spans[0].name == span_name_for_kind("custom")
        assert attrs[DIFY_NODE_KIND] == "custom"
        assert attrs[DIFY_NODE_KIND_RAW] == mock_custom_node.node_type
        assert attrs["gen_ai.span.kind"] == "TASK"

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_genai_handler_unavailable_degraded_path(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node
    ):
        """FR-006: when handler is None, LLM nodes still emit a span via tracer."""
        layer = ObservabilityLayer()
        layer.on_graph_start()
        layer.on_node_run_start(mock_llm_node)
        layer.on_node_run_end(mock_llm_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        # Span name normalised even on degraded path.
        assert spans[0].name == span_name_for_kind("llm")
        # Historical ``LLM`` value retained for backward-compatibility queries.
        assert attrs["gen_ai.span.kind"] == "LLM"


class TestObservabilityLayerParserIntegration:
    """Test parser integration for different node types."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_default_parser_used_for_regular_node(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_start_node
    ):
        """Test that default parser is used for non-tool nodes."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_start_node)
        layer.on_node_run_end(mock_start_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs["node.id"] == mock_start_node.id
        assert attrs["node.execution_id"] == mock_start_node.execution_id
        # ``node.type`` now stores canonical kind, not the BuiltinNodeTypes enum value.
        assert attrs["node.type"] == node_type_to_kind(mock_start_node.node_type)

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_tool_parser_used_for_tool_node(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_tool_node
    ):
        """Test that tool parser writes tool attributes (degraded path)."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_tool_node)
        layer.on_node_run_end(mock_tool_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs["node.id"] == mock_tool_node.id
        assert attrs["gen_ai.tool.name"] == mock_tool_node.title
        assert attrs["gen_ai.tool.type"] == mock_tool_node._node_data.provider_type.value

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_llm_parser_used_for_llm_node(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node, mock_result_event
    ):
        """LLM parser writes LLM attributes through the degraded path."""
        from graphon.node_events import NodeRunResult

        mock_result_event.node_run_result = NodeRunResult(
            inputs={},
            outputs={"text": "test completion", "finish_reason": "stop"},
            process_data={
                "model_name": "gpt-4",
                "model_provider": "openai",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "prompts": [{"role": "user", "text": "test prompt"}],
            },
            metadata={},
        )

        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_llm_node)
        layer.on_node_run_end(mock_llm_node, None, mock_result_event)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs["node.id"] == mock_llm_node.id
        assert attrs["gen_ai.request.model"] == "gpt-4"
        assert attrs["gen_ai.provider.name"] == "openai"
        assert attrs["gen_ai.usage.input_tokens"] == 10
        assert attrs["gen_ai.usage.output_tokens"] == 20
        assert attrs["gen_ai.usage.total_tokens"] == 30
        assert attrs["gen_ai.completion"] == "test completion"
        assert attrs["gen_ai.response.finish_reason"] == "stop"

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_retrieval_parser_used_for_retrieval_node(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_retrieval_node, mock_result_event
    ):
        """Retrieval parser writes retrieval attributes through the degraded path."""
        from graphon.node_events import NodeRunResult

        mock_result_event.node_run_result = NodeRunResult(
            inputs={"query": "test query"},
            outputs={"result": [{"content": "test content", "metadata": {"score": 0.9, "document_id": "doc1"}}]},
            process_data={},
            metadata={},
        )

        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_retrieval_node)
        layer.on_node_run_end(mock_retrieval_node, None, mock_result_event)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs["node.id"] == mock_retrieval_node.id
        assert attrs["retrieval.query"] == "test query"
        assert "retrieval.document" in attrs

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_result_event_extracts_inputs_and_outputs(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_start_node, mock_result_event
    ):
        """Test that result_event parameter allows parsers to extract inputs and outputs."""
        from graphon.node_events import NodeRunResult

        mock_result_event.node_run_result = NodeRunResult(
            inputs={"input_key": "input_value"},
            outputs={"output_key": "output_value"},
            process_data={},
            metadata={},
        )

        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_start_node)
        layer.on_node_run_end(mock_start_node, None, mock_result_event)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert "input.value" in attrs
        assert "output.value" in attrs


class TestObservabilityLayerGraphLifecycle:
    """Test graph lifecycle management."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_on_graph_start_clears_contexts(self, tracer_provider_with_memory_exporter, mock_llm_node):
        """Test that on_graph_start clears node contexts."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_llm_node)
        assert len(layer._node_contexts) == 1

        layer.on_graph_start()
        assert len(layer._node_contexts) == 0

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_on_graph_end_with_no_unfinished_spans(
        self, tracer_provider_with_memory_exporter, memory_span_exporter, mock_llm_node
    ):
        """Test that on_graph_end handles normal completion."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_llm_node)
        layer.on_node_run_end(mock_llm_node, None)
        layer.on_graph_end(None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 1

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", True)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_on_graph_end_with_unfinished_spans_logs_warning(
        self, tracer_provider_with_memory_exporter, mock_llm_node, caplog
    ):
        """Test that on_graph_end logs warning for unfinished spans."""
        layer = ObservabilityLayer()
        layer.on_graph_start()

        layer.on_node_run_start(mock_llm_node)
        assert len(layer._node_contexts) == 1

        layer.on_graph_end(None)

        assert len(layer._node_contexts) == 0
        assert "node spans were not properly ended" in caplog.text


class TestObservabilityLayerDisabledMode:
    """Test behavior when layer is disabled."""

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", False)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_disabled_mode_skips_node_start(self, memory_span_exporter, mock_start_node):
        """Test that disabled layer doesn't create spans on node start."""
        layer = ObservabilityLayer()
        assert layer._is_disabled

        layer.on_graph_start()
        layer.on_node_run_start(mock_start_node)
        layer.on_node_run_end(mock_start_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 0

    @patch("core.app.workflow.layers.observability.dify_config.ENABLE_OTEL", False)
    @pytest.mark.usefixtures("mock_is_instrument_flag_enabled_false", "force_genai_handler_unavailable")
    def test_disabled_mode_skips_node_end(self, memory_span_exporter, mock_llm_node):
        """Test that disabled layer doesn't process node end."""
        layer = ObservabilityLayer()
        assert layer._is_disabled

        layer.on_node_run_end(mock_llm_node, None)

        spans = memory_span_exporter.get_finished_spans()
        assert len(spans) == 0
