"""Lazy singleton getter for the LoongSuite ``ExtendedTelemetryHandler``.

Per spec FR-009 (3) / CS-002, the util-genai handler must:

- be loaded lazily so callers can import this module from anywhere safely;
- be a process-wide singleton, so we do not re-instantiate it per node;
- short-circuit to ``None`` when ``ENABLE_OTEL=False`` (and the legacy
  ``ENABLE_OTEL_FOR_INSTRUMENT`` flag is not set), so disabled deployments
  pay no cost;
- swallow any ``ImportError`` / construction error and return ``None`` so
  callers can fall back to ``tracer.start_span`` (FR-006).

The handler is read by ``ObservabilityLayer`` and (transitively) by parser
``build_invocation`` helpers; no other module is permitted to import the
``opentelemetry.util.genai`` namespace directly (CS-002).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from configs import dify_config
from extensions.otel.runtime import is_instrument_flag_enabled

if TYPE_CHECKING:
    # Imported only for type hints — never imported at module load time so we
    # don't fail-fast on environments missing the optional dep.
    from opentelemetry.util.genai.extended_handler import ExtendedTelemetryHandler

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_HANDLER: "ExtendedTelemetryHandler | None" = None
_HANDLER_INITIALIZED = False
_IMPORT_FAILED = False


def get_genai_handler() -> "ExtendedTelemetryHandler | None":
    """Return the process-wide ``ExtendedTelemetryHandler``, or ``None``.

    ``None`` is returned when:

    - OTel is disabled at config time;
    - the ``loongsuite-util-genai`` package is not installed;
    - the handler constructor raised an exception (logged once).

    Callers must be ready to fall back to ``tracer.start_span``.
    """
    if not (dify_config.ENABLE_OTEL or is_instrument_flag_enabled()):
        return None

    global _HANDLER, _HANDLER_INITIALIZED, _IMPORT_FAILED
    if _HANDLER_INITIALIZED:
        return _HANDLER
    with _LOCK:
        if _HANDLER_INITIALIZED:
            return _HANDLER
        try:
            # Local import — see module docstring for the dep convention.
            from opentelemetry.util.genai.extended_handler import (  # noqa: PLC0415
                get_extended_telemetry_handler,
            )

            _HANDLER = get_extended_telemetry_handler()
        except ImportError:
            _IMPORT_FAILED = True
            logger.info(
                "loongsuite-util-genai not installed; falling back to tracer.start_span "
                "for LLM/Retrieval/Tool/Agent spans (spec FR-006)."
            )
            _HANDLER = None
        except Exception:
            logger.warning(
                "Failed to construct util-genai ExtendedTelemetryHandler; "
                "falling back to tracer.start_span (spec FR-006).",
                exc_info=True,
            )
            _HANDLER = None
        finally:
            _HANDLER_INITIALIZED = True
    return _HANDLER


def reset_genai_handler() -> None:
    """Test-only helper: drop the cached handler so the next call re-runs init."""
    global _HANDLER, _HANDLER_INITIALIZED, _IMPORT_FAILED
    with _LOCK:
        _HANDLER = None
        _HANDLER_INITIALIZED = False
        _IMPORT_FAILED = False


def _set_handler_for_test(handler: Any) -> None:
    """Test-only helper: inject a fake handler without touching upstream package."""
    global _HANDLER, _HANDLER_INITIALIZED
    with _LOCK:
        _HANDLER = handler
        _HANDLER_INITIALIZED = True


__all__ = [
    "get_genai_handler",
    "reset_genai_handler",
]
