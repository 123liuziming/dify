"""Tests for the BuiltinNodeTypes -> canonical kind mapping (FR-002)."""

import pytest

from extensions.otel.semconv.node_kind import (
    CUSTOM_KIND,
    is_genai_handled_kind,
    kind_to_workflow_span_kind,
    node_type_to_kind,
    span_name_for_kind,
)
from graphon.enums import BuiltinNodeTypes


class TestNodeTypeToKind:
    """Every BuiltinNodeTypes value resolves to a deterministic kebab-case kind."""

    @pytest.mark.parametrize("node_type", list(BuiltinNodeTypes))
    def test_every_builtin_resolves_to_kebab_case(self, node_type):
        kind = node_type_to_kind(node_type)
        assert kind != CUSTOM_KIND
        # Canonical kind: lowercase, dashes only, ASCII.
        assert kind.islower()
        assert kind.replace("-", "").isalnum()

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("my-plugin:custom", CUSTOM_KIND),
            ("", CUSTOM_KIND),
            (None, CUSTOM_KIND),
            (123, CUSTOM_KIND),
        ],
    )
    def test_unknown_inputs_fall_back_to_custom(self, raw, expected):
        assert node_type_to_kind(raw) == expected


class TestSpanKindMapping:
    """Spec FR-003 / Appendix A: ``gen_ai.span.kind`` derivation."""

    def test_genai_handled_kinds_have_empty_workflow_value(self):
        for kind in ("llm", "knowledge-retrieval", "tool", "agent"):
            assert is_genai_handled_kind(kind) is True
            assert kind_to_workflow_span_kind(kind) == ""

    @pytest.mark.parametrize("node_type", list(BuiltinNodeTypes))
    def test_workflow_kinds_emit_workflow_prefix_or_genai_handled(self, node_type):
        kind = node_type_to_kind(node_type)
        if is_genai_handled_kind(kind):
            return
        value = kind_to_workflow_span_kind(kind)
        assert value.startswith("WORKFLOW_"), f"{kind} -> {value}"

    def test_custom_falls_back_to_task(self):
        assert kind_to_workflow_span_kind(CUSTOM_KIND) == "TASK"


class TestSpanNameForKind:
    @pytest.mark.parametrize("kind", ["llm", "if-else", "code", "custom"])
    def test_span_name_prefix(self, kind):
        assert span_name_for_kind(kind) == f"dify:{kind}"
