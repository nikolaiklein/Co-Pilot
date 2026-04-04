"""
Qdrant collection management for C60 Fullerene Memory.

Brandur — Principle 1: Constraints are assertions.
Collection schema, indexes, and UUID mapping are defined here
and created before the first write.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    VectorParams,
)

logger = logging.getLogger(__name__)

# Collection for C60 atoms — separate from legacy copilot_memory
C60_COLLECTION = "copilot_c60_memory"

# gemini-embedding-001 output dimension (3072-dim, released 2025)
VECTOR_SIZE = 3072

# UUID5 namespace for deterministic Qdrant point IDs
_UUID5_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL


def node_id_to_qdrant_uuid(node_id: str) -> str:
    """
    Convert a C60Atom node_id to a deterministic Qdrant point UUID.

    UUID5(NAMESPACE_URL, node_id) — deterministic, collision-resistant.
    NEVER use the returned UUID as a node_id reference in covalent_bonds.
    covalent_bonds.target_node always references the human node_id, not this UUID.
    """
    return str(uuid.uuid5(_UUID5_NAMESPACE, node_id))


async def ensure_collection(client: AsyncQdrantClient) -> None:
    """
    Idempotent collection bootstrap: creates copilot_c60_memory with
    payload indexes if it does not already exist.

    Indexes are created BEFORE the first write (Brandur P1: constraints are assertions).
    Existing collection is left untouched.
    """
    collections = await client.get_collections()
    existing = {c.name for c in collections.collections}

    if C60_COLLECTION not in existing:
        await client.create_collection(
            collection_name=C60_COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"Created Qdrant collection '{C60_COLLECTION}' ({VECTOR_SIZE}-dim, cosine)")
    else:
        logger.debug(f"Qdrant collection '{C60_COLLECTION}' already exists")

    # Ensure payload indexes — idempotent, safe to call repeatedly
    # Index 1: user_id — keyword, for all per-user operations
    await client.create_payload_index(
        collection_name=C60_COLLECTION,
        field_name="user_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    # Index 2: node_id — keyword, for bond traversal lookups
    await client.create_payload_index(
        collection_name=C60_COLLECTION,
        field_name="node_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    logger.info(f"Qdrant payload indexes ensured on '{C60_COLLECTION}' (user_id, node_id)")


def build_user_filter(user_id: int) -> Filter:
    """Returns a Qdrant filter restricting results to a single user."""
    return Filter(
        must=[FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))]
    )


def build_node_id_filter(user_id: int, node_ids: list[str]) -> Filter:
    """Returns a Qdrant filter for specific node_ids belonging to a user."""
    from qdrant_client.models import MatchAny

    return Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
            FieldCondition(key="node_id", match=MatchAny(any=node_ids)),
        ]
    )
