"""
Shared fixtures for ObservabilityLayer tests.
"""

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import set_tracer_provider

from graphon.enums import BuiltinNodeTypes


@pytest.fixture
def memory_span_exporter():
    """Provide an in-memory span exporter for testing."""
    return InMemorySpanExporter()


@pytest.fixture
def tracer_provider_with_memory_exporter(memory_span_exporter):
    """Provide a TracerProvider configured with memory exporter."""
    import opentelemetry.trace as trace_api

    trace_api._TRACER_PROVIDER = None
    trace_api._TRACER_PROVIDER_SET_ONCE._done = False

    provider = TracerProvider()
    processor = SimpleSpanProcessor(memory_span_exporter)
    provider.add_span_processor(processor)
    set_tracer_provider(provider)

    yield provider

    provider.force_flush()


@pytest.fixture
def mock_start_node():
    """Create a mock Start Node."""
    node = MagicMock()
    node.id = "test-start-node-id"
    node.title = "Start Node"
    node.execution_id = "test-start-execution-id"
    node.node_type = BuiltinNodeTypes.START
    return node


@pytest.fixture
def mock_llm_node():
    """Create a mock LLM Node."""
    node = MagicMock()
    node.id = "test-llm-node-id"
    node.title = "LLM Node"
    node.execution_id = "test-llm-execution-id"
    node.node_type = BuiltinNodeTypes.LLM
    return node


@pytest.fixture
def mock_tool_node():
    """Create a mock Tool Node with tool-specific attributes."""
    from core.tools.entities.tool_entities import ToolProviderType
    from graphon.nodes.tool.entities import ToolNodeData

    node = MagicMock()
    node.id = "test-tool-node-id"
    node.title = "Test Tool Node"
    node.execution_id = "test-tool-execution-id"
    node.node_type = BuiltinNodeTypes.TOOL

    tool_data = ToolNodeData(
        title="Test Tool Node",
        desc=None,
        provider_id="test-provider-id",
        provider_type=ToolProviderType.BUILT_IN,
        provider_name="test-provider",
        tool_name="test-tool",
        tool_label="Test Tool",
        tool_configurations={},
        tool_parameters={},
    )
    node._node_data = tool_data

    return node


@pytest.fixture
def mock_is_instrument_flag_enabled_false():
    """Mock is_instrument_flag_enabled to return False."""
    with patch("core.app.workflow.layers.observability.is_instrument_flag_enabled", return_value=False, autospec=True):
        yield


@pytest.fixture
def mock_is_instrument_flag_enabled_true():
    """Mock is_instrument_flag_enabled to return True."""
    with patch("core.app.workflow.layers.observability.is_instrument_flag_enabled", return_value=True, autospec=True):
        yield


@pytest.fixture
def mock_retrieval_node():
    """Create a mock Knowledge Retrieval Node."""
    node = MagicMock()
    node.id = "test-retrieval-node-id"
    node.title = "Retrieval Node"
    node.execution_id = "test-retrieval-execution-id"
    node.node_type = BuiltinNodeTypes.KNOWLEDGE_RETRIEVAL
    return node


@pytest.fixture
def mock_agent_node():
    """Create a mock Agent Node."""
    node = MagicMock()
    node.id = "test-agent-node-id"
    node.title = "Agent Node"
    node.execution_id = "test-agent-execution-id"
    node.node_type = BuiltinNodeTypes.AGENT
    return node


@pytest.fixture
def mock_custom_node():
    """Create a mock Custom Node whose ``node_type`` is not in BuiltinNodeTypes."""
    node = MagicMock()
    node.id = "test-custom-node-id"
    node.title = "Custom Node"
    node.execution_id = "test-custom-execution-id"
    node.node_type = "my-plugin:my-custom-node"
    return node


@pytest.fixture
def node_factory():
    """Factory that builds a minimal mock node for any BuiltinNodeTypes value."""

    def _make(node_type: BuiltinNodeTypes):
        node = MagicMock()
        node.id = f"test-{node_type.name.lower()}-node-id"
        node.title = f"{node_type.name} Node"
        node.execution_id = f"test-{node_type.name.lower()}-execution-id"
        node.node_type = node_type
        if node_type == BuiltinNodeTypes.TOOL:
            from core.tools.entities.tool_entities import ToolProviderType
            from graphon.nodes.tool.entities import ToolNodeData

            node._node_data = ToolNodeData(
                title=node.title,
                desc=None,
                provider_id="test-provider-id",
                provider_type=ToolProviderType.BUILT_IN,
                provider_name="test-provider",
                tool_name="test-tool",
                tool_label="Test Tool",
                tool_configurations={},
                tool_parameters={},
            )
        return node

    return _make


@pytest.fixture
def force_genai_handler_unavailable():
    """Force ``get_genai_handler()`` to return None for the duration of a test.

    This drives the FR-006 degraded path so unit tests can run without the
    optional ``loongsuite-util-genai`` dependency installed.
    """
    with patch(
        "core.app.workflow.layers.observability.get_genai_handler",
        return_value=None,
        autospec=True,
    ):
        yield


@pytest.fixture
def mock_result_event():
    """Create a mock result event with NodeRunResult."""
    from datetime import datetime

    from graphon.graph_events import NodeRunSucceededEvent
    from graphon.node_events import NodeRunResult

    node_run_result = NodeRunResult(
        inputs={"query": "test query"},
        outputs={"result": [{"content": "test content", "metadata": {}}]},
        process_data={},
        metadata={},
    )

    return NodeRunSucceededEvent(
        id="test-execution-id",
        node_id="test-node-id",
        node_type=BuiltinNodeTypes.LLM,
        start_at=datetime.now(),
        node_run_result=node_run_result,
    )
