"""Domain errors that carry a stable machine-readable code and a safe, user-facing message."""

from __future__ import annotations

from typing import Any


class TripScopeError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(TripScopeError):
    status_code = 404
    code = "not_found"


class AuthenticationError(TripScopeError):
    status_code = 401
    code = "not_authenticated"


class PermissionDeniedError(TripScopeError):
    status_code = 403
    code = "forbidden"


class ConflictError(TripScopeError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(TripScopeError):
    status_code = 422
    code = "validation_failed"


class ServiceUnavailableError(TripScopeError):
    status_code = 503
    code = "service_unavailable"


class AIUnavailableError(TripScopeError):
    """The AI analyst is disabled or its provider is not configured."""

    status_code = 503
    code = "ai_unavailable"


class AIProviderError(TripScopeError):
    """The language-model provider failed, timed out or returned something unusable. Never carries keys."""

    status_code = 502
    code = "ai_provider_failed"


class QueryFailedError(TripScopeError):
    """An analytics query failed or exceeded a server-side limit. Never carries SQL or credentials."""

    status_code = 502
    code = "analytics_query_failed"


class PipelineError(TripScopeError):
    code = "pipeline_failed"


class SourceValidationError(PipelineError):
    code = "source_validation_failed"
