"""
C60 Fullerene Memory — typed value objects.

C60Atom: fundamental unit of C60 memory.
C60Bond: directed weighted relationship between atoms.

Design constraints:
- covalent_bonds MUST be exactly 3 (hard invariant — no padding/trimming)
- All timestamps in UTC with timezone info
- pentagon_domain validated against allowlist regex
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Relation types forming the C60 semantic graph
RelationType = Literal["РАЗВИВАЕТ", "ПРОТИВОРЕЧИТ", "ВЛИЯЕТ_НА", "ЧАСТЬ_ОТ"]

# Allowlist: Russian/Latin letters, digits, spaces, hyphens, underscores, parens, slash
_DOMAIN_RE = re.compile(r"^[А-Яа-яёЁA-Za-z0-9 \-_()/]+$")


def _ensure_utc(v: datetime) -> datetime:
    """Normalise datetime to UTC-aware."""
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


class C60Bond(BaseModel):
    """Directed weighted edge in the C60 memory graph."""

    model_config = ConfigDict(frozen=True)

    target_node: str = Field(..., min_length=1, description="node_id of the target C60Atom")
    relation_type: RelationType = Field(..., description="Semantic relation type")
    weight: float = Field(..., ge=0.0, le=1.0, description="Bond strength 0.0–1.0")
    last_activated: datetime = Field(..., description="UTC timestamp of last activation")

    @field_validator("last_activated", mode="after")
    @classmethod
    def _normalise_last_activated(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class C60Atom(BaseModel):
    """
    Fundamental memory unit — C60 Atom.

    Invariants (enforced at model level):
    - covalent_bonds must be exactly 3 (hard invariant)
    - pentagon_domain matches allowlist regex
    - vector_core is at least 10 characters
    - All timestamps are UTC-aware
    """

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(..., min_length=1, description="Globally unique atom identifier")
    pentagon_domain: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Personal life domain this atom belongs to",
    )
    vector_core: str = Field(
        ...,
        min_length=10,
        description="Canonical text for embedding — distilled essence of the memory",
    )
    covalent_bonds: list[C60Bond] = Field(
        ...,
        description="Exactly 3 semantic bonds to other atoms",
    )
    is_shadow: bool = Field(
        default=False,
        description="True when all bonds have decayed — atom is archived, not deleted",
    )
    created_at: datetime = Field(..., description="UTC creation timestamp")

    @field_validator("pentagon_domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        if not _DOMAIN_RE.match(v):
            raise ValueError(
                f"pentagon_domain contains invalid characters: {v!r}. "
                "Allowed: Russian/Latin letters, digits, spaces, hyphens, underscores, parens, slash."
            )
        return v

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalise_created_at(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _enforce_bond_count(self) -> "C60Atom":
        """Hard invariant: C60 topology requires exactly 3 bonds per atom."""
        if len(self.covalent_bonds) != 3:
            raise ValueError(
                f"C60Atom invariant violated: expected exactly 3 covalent_bonds, "
                f"got {len(self.covalent_bonds)}. Do NOT pad or trim — fix the LLM output."
            )
        return self
