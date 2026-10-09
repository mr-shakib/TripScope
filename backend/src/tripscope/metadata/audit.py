"""Audit trail for security-relevant and administrative actions (FR-01)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from tripscope.core.logging import request_id_var
from tripscope.metadata.models import AuditEvent


def record_audit(
    session: Session,
    *,
    action: str,
    outcome: str,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    client_ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            outcome=outcome,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            target_type=target_type,
            target_id=target_id,
            client_ip=client_ip,
            request_id=request_id_var.get(),
            details=details or {},
        )
    )
