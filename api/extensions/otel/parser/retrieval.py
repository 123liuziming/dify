"""
Parser for knowledge retrieval nodes that captures retrieval-specific metadata.

Spec ref: spec/dify-workflow-span-naming/spec.md FR-009 (2) — retrieval spans
are now emitted via ``handler.start_retrieval`` / ``stop_retrieval`` driven by
``build_invocation``. ``parse`` is kept for the FR-006 degraded path.
"""

import logging
from collections.abc import Sequence
from typing import Any

from opentelemetry.trace import Span

from extensions.otel.parser.base import (
    DefaultNodeOTelParser,
    _set_common_node_attributes,
    safe_json_dumps,
    should_include_content,
)
from extensions.otel.semconv.gen_ai import GenAIAttributes, RetrieverAttributes
from extensions.otel.semconv.node_kind import node_type_to_kind
from graphon.graph_events import GraphNodeEventBase
from graphon.nodes.base.node import Node
from graphon.variables import Segment

logger = logging.getLogger(__name__)


def _format_retrieval_documents(retrieval_documents: list[Any]) -> list:
    """
    Format retrieval documents for semantic conventions.

    Args:
        retrieval_documents: List of retrieval document dictionaries

    Returns:
        List of formatted semantic documents
    """
    try:
        if not isinstance(retrieval_documents, list):
            return []

        semantic_documents = []
        for doc in retrieval_documents:
            if not isinstance(doc, dict):
                continue

            metadata = doc.get("metadata", {})
            content = doc.get("content", "")
            title = doc.get("title", "")
            score = metadata.get("score", 0.0)
            document_id = metadata.get("document_id", "")

            semantic_metadata = {}
            if title:
                semantic_metadata["title"] = title
            if metadata.get("source"):
                semantic_metadata["source"] = metadata["source"]
            elif metadata.get("_source"):
                semantic_metadata["source"] = metadata["_source"]
            if metadata.get("doc_metadata"):
                doc_metadata = metadata["doc_metadata"]
                if isinstance(doc_metadata, dict):
                    semantic_metadata.update(doc_metadata)

            semantic_doc = {
                "document": {"content": content, "metadata": semantic_metadata, "score": score, "id": document_id}
            }
            semantic_documents.append(semantic_doc)

        return semantic_documents
    except Exception as e:
        logger.warning("Failed to format retrieval documents: %s", e, exc_info=True)
        return []


def _coerce_documents_for_invocation(retrieval_documents: list[Any]) -> list:
    """Build a list of util-genai ``RetrievalDocument`` instances.

    Returns ``[]`` when util-genai is unavailable so callers can skip the
    invocation path (FR-006).
    """
    try:
        from opentelemetry.util.genai.extended_types import RetrievalDocument  # noqa: PLC0415
    except ImportError:
        return []

    docs: list = []
    if not isinstance(retrieval_documents, list):
        return docs
    for doc in retrieval_documents:
        if not isinstance(doc, dict):
            continue
        metadata = doc.get("metadata", {}) or {}
        score = metadata.get("score", 0.0) or 0.0
        document_id = metadata.get("document_id", "") or ""
        docs.append(
            RetrievalDocument(
                id=str(document_id) if document_id else "",
                score=float(score) if score else 0.0,
                content=doc.get("content", "") or "",
                metadata={k: v for k, v in metadata.items() if not k.startswith("_")},
            )
        )
    return docs


def _extract_retrieval_documents(outputs: dict) -> list:
    """Pull a list of retrieval document dicts out of node ``outputs``."""
    if not outputs:
        return []
    result_value = outputs.get("result")
    if result_value is None:
        return []
    value_to_check = result_value
    if isinstance(result_value, Segment):
        value_to_check = result_value.value
    if isinstance(value_to_check, (list, Sequence)):
        return list(value_to_check)
    return []


class RetrievalNodeOTelParser:
    """Parser for knowledge retrieval nodes that captures retrieval-specific metadata."""

    def __init__(self) -> None:
        self._delegate = DefaultNodeOTelParser()

    def build_invocation(
        self,
        *,
        node: Node,
        result_event: GraphNodeEventBase | None = None,
    ) -> Any | None:
        try:
            from opentelemetry.util.genai.extended_types import RetrievalInvocation  # noqa: PLC0415
        except ImportError:
            return None

        invocation = RetrievalInvocation()
        if not result_event or not result_event.node_run_result:
            return invocation

        node_run_result = result_event.node_run_result
        inputs = node_run_result.inputs or {}
        outputs = node_run_result.outputs or {}

        if should_include_content():
            query = str(inputs.get("query", "")) if inputs else ""
            if query:
                invocation.query = query
            documents = _extract_retrieval_documents(outputs)
            if documents:
                invocation.documents = _coerce_documents_for_invocation(documents)
        return invocation

    def parse(
        self, *, node: Node, span: "Span", error: Exception | None, result_event: GraphNodeEventBase | None = None
    ) -> None:
        """FR-006 degraded path: write retrieval attrs directly to a tracer-owned span."""
        kind = node_type_to_kind(node.node_type)
        _set_common_node_attributes(node=node, span=span, kind=kind)
        span.set_attribute(GenAIAttributes.SPAN_KIND, "RETRIEVER")

        if not result_event or not result_event.node_run_result:
            return

        node_run_result = result_event.node_run_result
        inputs = node_run_result.inputs or {}
        outputs = node_run_result.outputs or {}

        if not should_include_content():
            return

        query = str(inputs.get("query", "")) if inputs else ""
        if query:
            span.set_attribute(RetrieverAttributes.QUERY, query)

        retrieval_documents = _extract_retrieval_documents(outputs)
        if retrieval_documents:
            semantic_retrieval_documents = _format_retrieval_documents(retrieval_documents)
            semantic_retrieval_documents_json = safe_json_dumps(semantic_retrieval_documents)
            span.set_attribute(RetrieverAttributes.DOCUMENT, semantic_retrieval_documents_json)
