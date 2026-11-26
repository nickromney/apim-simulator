"""Shared rules for management API request bodies.

A client may PUT back what a GET returned (``apim-sim put-api``, a script that
edits JSON). Fields the server computes, such as ``id`` and ``resource_id``,
are ignored there, as Azure Resource Manager ignores read-only properties. Any
other field the body does not define is rejected with 422, so a client never
records a value the server silently dropped.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

# What every GET puts in place of a secret. A PUT that sends it back keeps the
# stored secret instead of saving the mask.
SECRET_MASK = "***"

# Read-only fields that most resource projections add.
_COMMON_READ_ONLY = frozenset({"id", "resource_id", "policy_scope"})


class ManagementBody(BaseModel):
    """Base for PUT and POST bodies: read-only fields ignored, unknown fields rejected."""

    model_config = ConfigDict(extra="forbid")

    # Fields this resource's GET returns but its PUT does not write.
    read_only: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _ignore_read_only(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ignored = (_COMMON_READ_ONLY | cls.read_only) - cls.model_fields.keys()
        return {key: value for key, value in data.items() if key not in ignored}


def is_mask(value: Any) -> bool:
    return value == SECRET_MASK
