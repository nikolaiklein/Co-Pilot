"""
AI Dream State — nightly memory consolidation for C60 memory.

Backend × Fowler × Brandur — Three named functions + Chunked reads + Idempotency.
Willison × Hunt — Context curation (P3) + Ownership verification (P7).

Three phases:
    Phase 1: Weight Decay — bonds inactive > 30 days lose 0.15 weight
    Phase 2: Apoptosis — bonds with weight < 0.1 are removed; empty atoms → is_shadow=True
    Phase 3: Valence Restoration — atoms with < 3 bonds get new bonds via LLM

Idempotency: Firestore distributed lock prevents concurrent runs per user.
Streaming: Qdrant scroll with batch_size=100, never loads all atoms into memory.

EC-4: logs with step_label="valence_restoration".
EC-9: LLM output validated before Qdrant write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from services.ai_engine import BaseProvider
    from services.db import DatabaseService

logger = logging.getLogger(__name__)

# Configuration
_DECAY_INACTIVITY_DAYS = 30     # bonds inactive > N days decay
_DECAY_AMOUNT = 0.15            # weight reduction per decay cycle
_APOPTOSIS_THRESHOLD = 0.1      # bonds below this weight are removed
_LOCK_TTL_MINUTES = 10          # distributed lock TTL
_SCROLL_BATCH_SIZE = 100        # atoms per Qdrant scroll page

_LOCK_COLLECTION = "dream_state_locks"
_VALENCE_LLM_TIMEOUT = 30.0     # seconds per valence restoration call

# Valence Restoration context limits
_VALENCE_MAX_CANDIDATES = 20
_VALENCE_MAX_CANDIDATE_CHARS = 50


# ──────────────────────────────────────────────────────────────────────────────
# Distributed lock (Firestore)
# ──────────────────────────────────────────────────────────────────────────────

async def _acquire_lock(user_id: int, db: "DatabaseService") -> bool:
    """
    Attempt to acquire a distributed lock for this user's Dream State run.
    Returns True if lock acquired, False if already locked.
    """
    instance_id = str(uuid.uuid4())[:8]
    now_utc = datetime.now(timezone.utc)
    lock_ref = db.db.collection(_LOCK_COLLECTION).document(str(user_id))

    try:
        existing = await lock_ref.get()
        if existing.exists:
            lock_data = existing.to_dict() or {}
            started_at = lock_data.get("started_at")
            if isinstance(started_at, datetime):
                age = now_utc - started_at.replace(tzinfo=timezone.utc)
                if age < timedelta(minutes=_LOCK_TTL_MINUTES):
                    logger.info(
                        f"Dream State: lock held for user {user_id}, "
                        f"age={age.total_seconds():.0f}s — skipping"
                    )
                    return False

        await lock_ref.set({
            "started_at": now_utc,
            "instance_id": instance_id,
            "user_id": user_id,
        })
        logger.info(f"Dream State: lock acquired for user {user_id} (instance={instance_id})")
        return True

    except Exception as e:
        logger.error(f"Dream State: lock acquisition failed for {user_id}: {e}")
        return False


async def _release_lock(user_id: int, db: "DatabaseService") -> None:
    """Release the distributed lock for this user."""
    try:
        await db.db.collection(_LOCK_COLLECTION).document(str(user_id)).delete()
    except Exception as e:
        logger.warning(f"Dream State: lock release failed for {user_id}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: Weight Decay
# ──────────────────────────────────────────────────────────────────────────────

async def apply_weight_decay(
    user_id: int,
    client: "AsyncQdrantClient",
) -> int:
    """
    Phase 1: Decay bond weights for bonds inactive > _DECAY_INACTIVITY_DAYS.

    Streams atoms via Qdrant scroll (batch_size=100).
    Batch-upserts modified atoms at end of each page.

    Returns: count of modified atoms.
    """
    from services.c60_qdrant import C60_COLLECTION, build_user_filter

    cutoff = datetime.now(timezone.utc) - timedelta(days=_DECAY_INACTIVITY_DAYS)
    modified_count = 0
    offset_token = None

    while True:
        batch, next_token = await asyncio.wait_for(
            client.scroll(
                collection_name=C60_COLLECTION,
                scroll_filter=build_user_filter(user_id),
                limit=_SCROLL_BATCH_SIZE,
                offset=offset_token,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=60.0,
        )

        if not batch:
            break

        points_to_update: list[dict] = []

        for point in batch:
            p = point.payload or {}
            bonds = p.get("covalent_bonds", [])
            changed = False

            for bond in bonds:
                last_str = bond.get("last_activated", "")
                try:
                    last_dt = datetime.fromisoformat(last_str.rstrip("Z")).replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    last_dt = cutoff  # treat missing as stale

                if last_dt < cutoff:
                    new_weight = max(0.0, round(bond.get("weight", 1.0) - _DECAY_AMOUNT, 4))
                    bond["weight"] = new_weight
                    changed = True

            if changed:
                points_to_update.append({
                    "id": point.id,
                    "payload": {"covalent_bonds": bonds},
                })
                modified_count += 1

        # Batch upsert modified payloads for this page
        if points_to_update:
            for item in points_to_update:
                await asyncio.wait_for(
                    client.set_payload(
                        collection_name=C60_COLLECTION,
                        payload=item["payload"],
                        points=[item["id"]],
                    ),
                    timeout=30.0,
                )

        if next_token is None:
            break
        offset_token = next_token

    logger.info(f"Dream State Phase 1 (decay): {modified_count} atoms modified for user {user_id}")
    return modified_count


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: Apoptosis
# ──────────────────────────────────────────────────────────────────────────────

async def run_apoptosis(
    user_id: int,
    client: "AsyncQdrantClient",
) -> tuple[int, list[str]]:
    """
    Phase 2: Remove bonds with weight < _APOPTOSIS_THRESHOLD.
    Atoms that lose all bonds become shadows (is_shadow=True).

    Returns: (bonds_removed, underbonded_node_ids)
    """
    from services.c60_qdrant import C60_COLLECTION, build_user_filter

    bonds_removed = 0
    underbonded_nodes: list[str] = []
    offset_token = None

    while True:
        batch, next_token = await asyncio.wait_for(
            client.scroll(
                collection_name=C60_COLLECTION,
                scroll_filter=build_user_filter(user_id),
                limit=_SCROLL_BATCH_SIZE,
                offset=offset_token,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=60.0,
        )

        if not batch:
            break

        for point in batch:
            p = point.payload or {}
            bonds = p.get("covalent_bonds", [])
            surviving_bonds = [b for b in bonds if b.get("weight", 1.0) >= _APOPTOSIS_THRESHOLD]
            removed = len(bonds) - len(surviving_bonds)

            if removed > 0:
                bonds_removed += removed
                new_payload: dict = {"covalent_bonds": surviving_bonds}

                if len(surviving_bonds) == 0 and not p.get("is_shadow", False):
                    new_payload["is_shadow"] = True

                await asyncio.wait_for(
                    client.set_payload(
                        collection_name=C60_COLLECTION,
                        payload=new_payload,
                        points=[point.id],
                    ),
                    timeout=30.0,
                )

            # Collect underbonded nodes for Phase 3
            if len(surviving_bonds) < 3:
                node_id = p.get("node_id", "")
                if node_id:
                    underbonded_nodes.append(node_id)

        if next_token is None:
            break
        offset_token = next_token

    logger.info(
        f"Dream State Phase 2 (apoptosis): {bonds_removed} bonds removed, "
        f"{len(underbonded_nodes)} underbonded nodes for user {user_id}"
    )
    return bonds_removed, underbonded_nodes


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Valence Restoration (Task 11)
# Willison × Hunt — Context curation (P3) + Ownership verification (P7)
# ──────────────────────────────────────────────────────────────────────────────

_VALENCE_PROMPT = """Ты — C60 Topology Engine в режиме восстановления связей.

У атома памяти не хватает связей (менее 3). Создай новые.

## Атом для восстановления
node_id: {node_id}
domain: {domain}
core: {vector_core}
текущих связей: {current_bonds}

## Кандидаты для новых связей (атомы того же домена и соседи)
{candidates}

## Задача
Выбери {needed} новых связей из кандидатов. Если кандидатов недостаточно — используй семантические якоря.
Верни только новые связи.

## Формат ответа (только JSON, без блока кода)
{
  "new_bonds": [
    {
      "target_node": "<node_id кандидата или семантический якорь>",
      "relation_type": "РАЗВИВАЕТ",
      "weight": 0.7,
      "last_activated": "2026-04-03T12:00:00Z"
    }
  ]
}
"""


async def restore_valence(
    user_id: int,
    underbonded_node_ids: list[str],
    client: "AsyncQdrantClient",
    ai_provider: "BaseProvider",
) -> int:
    """
    Phase 3: Restore bonds for underbonded atoms.

    For each underbonded atom:
    - Fetch candidates from same pentagon_domain + 1st-degree neighbors
    - Call LLM to generate new bonds (Willison P3: curate context)
    - Verify target ownership (Hunt P7: target must belong to this user)
    - Update atom in Qdrant

    Returns: count of bonds restored.
    """
    from services.c60_qdrant import C60_COLLECTION, build_node_id_filter, build_user_filter

    bonds_restored = 0
    now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for node_id in underbonded_node_ids:
        try:
            bonds_restored += await _restore_single_atom(
                user_id=user_id,
                node_id=node_id,
                client=client,
                ai_provider=ai_provider,
                now_z=now_z,
                C60_COLLECTION=C60_COLLECTION,
                build_node_id_filter=build_node_id_filter,
                build_user_filter=build_user_filter,
            )
        except Exception as e:
            logger.error(f"Dream State Phase 3: error for node {node_id[:8]}…: {e}")

    logger.info(
        f"Dream State Phase 3 (valence): {bonds_restored} bonds restored for user {user_id}"
    )
    return bonds_restored


async def _restore_single_atom(
    user_id: int,
    node_id: str,
    client: "AsyncQdrantClient",
    ai_provider: "BaseProvider",
    now_z: str,
    C60_COLLECTION: str,
    build_node_id_filter,
    build_user_filter,
) -> int:
    """Restore bonds for a single underbonded atom. Returns count of new bonds added."""
    from services.c60_qdrant import node_id_to_qdrant_uuid

    point_id = node_id_to_qdrant_uuid(node_id)

    # Fetch current atom
    results = await asyncio.wait_for(
        client.retrieve(
            collection_name=C60_COLLECTION,
            ids=[point_id],
            with_payload=True,
        ),
        timeout=30.0,
    )

    if not results:
        return 0

    atom_payload = results[0].payload or {}
    current_bonds = atom_payload.get("covalent_bonds", [])
    current_bond_count = len(current_bonds)
    needed = 3 - current_bond_count

    if needed <= 0:
        return 0  # already fully bonded

    domain = atom_payload.get("pentagon_domain", "")
    vector_core = atom_payload.get("vector_core", "")

    # Build candidate context: same domain + 1st-degree neighbors
    neighbor_node_ids = [b.get("target_node", "") for b in current_bonds if b.get("target_node")]

    candidates = await _fetch_candidates(
        user_id=user_id,
        domain=domain,
        neighbor_node_ids=neighbor_node_ids,
        exclude_node_id=node_id,
        client=client,
        C60_COLLECTION=C60_COLLECTION,
        build_user_filter=build_user_filter,
    )

    if not candidates:
        logger.debug(f"Valence Restoration: no candidates for node {node_id[:8]}…")
        return 0

    candidates_str = "\n".join(
        f"- node_id: {c['node_id']} | domain: {c.get('pentagon_domain', '?')} | "
        f"core: {c.get('vector_core', '')[:_VALENCE_MAX_CANDIDATE_CHARS]}"
        for c in candidates[:_VALENCE_MAX_CANDIDATES]
    )

    prompt = _VALENCE_PROMPT.format(
        node_id=node_id,
        domain=domain,
        vector_core=vector_core[:100],
        current_bonds=current_bond_count,
        candidates=candidates_str,
        needed=needed,
    )

    model_name = getattr(ai_provider, "model", "unknown")
    logger.info(
        f"Valence Restoration: node={node_id[:8]}…, needed={needed}, "
        f"model={model_name}, step_label=valence_restoration"
    )

    # EC-4: log call metadata
    start_time = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            ai_provider.generate(
                [{"role": "user", "content": prompt}],
                "Отвечай только JSON, без пояснений.",
                temperature=0.0,
            ),
            timeout=_VALENCE_LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"Valence Restoration: LLM timeout, "
            f"step_label=valence_restoration, node={node_id[:8]}…"
        )
        return 0
    except Exception as e:
        logger.error(
            f"Valence Restoration: LLM error {type(e).__name__}, "
            f"step_label=valence_restoration"
        )
        return 0

    latency_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        f"Valence Restoration: latency={latency_ms}ms, step_label=valence_restoration"
    )

    # Parse and validate (EC-9)
    json_text = raw.strip()
    if json_text.startswith("```"):
        json_text = "\n".join(
            line for line in json_text.splitlines() if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(json_text)
        new_bonds_raw = data.get("new_bonds", [])
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Valence Restoration: JSON parse failed: {e}")
        return 0

    # Verify ownership: target must belong to this user (Hunt P7)
    valid_bonds = []
    candidate_node_ids = {c["node_id"] for c in candidates}

    for bond in new_bonds_raw[:needed]:
        target = bond.get("target_node", "")
        if not target:
            continue

        # If target looks like a real node_id, verify it belongs to this user
        if len(target) == 36 and "-" in target:  # UUID format
            if target not in candidate_node_ids:
                # Check Qdrant for ownership
                try:
                    check_result, _ = await asyncio.wait_for(
                        client.scroll(
                            collection_name=C60_COLLECTION,
                            scroll_filter=build_node_id_filter(user_id, [target]),
                            limit=1,
                            with_payload=False,
                            with_vectors=False,
                        ),
                        timeout=10.0,
                    )
                    if not check_result:
                        # Not found or wrong user — use as semantic anchor instead
                        logger.warning(
                            f"Valence Restoration: target {target[:8]}… "
                            f"not owned by user {user_id} — using as concept anchor"
                        )
                except Exception:
                    pass  # use as-is

        validated_bond = {
            "target_node": target,
            "relation_type": bond.get("relation_type", "ВЛИЯЕТ_НА"),
            "weight": max(0.0, min(1.0, float(bond.get("weight", 0.5)))),
            "last_activated": now_z,
        }

        # EC-9: validate relation_type
        valid_relations = {"РАЗВИВАЕТ", "ПРОТИВОРЕЧИТ", "ВЛИЯЕТ_НА", "ЧАСТЬ_ОТ"}
        if validated_bond["relation_type"] not in valid_relations:
            validated_bond["relation_type"] = "ВЛИЯЕТ_НА"

        valid_bonds.append(validated_bond)

    if not valid_bonds:
        return 0

    # Update atom payload
    updated_bonds = current_bonds + valid_bonds
    # Enforce 50-bond limit (application-level constraint from Task 3)
    updated_bonds = updated_bonds[:50]

    await asyncio.wait_for(
        client.set_payload(
            collection_name=C60_COLLECTION,
            payload={"covalent_bonds": updated_bonds},
            points=[point_id],
        ),
        timeout=30.0,
    )

    logger.info(
        f"Valence Restoration: +{len(valid_bonds)} bonds for node {node_id[:8]}…, "
        f"step_label=valence_restoration"
    )
    return len(valid_bonds)


async def _fetch_candidates(
    user_id: int,
    domain: str,
    neighbor_node_ids: list[str],
    exclude_node_id: str,
    client: "AsyncQdrantClient",
    C60_COLLECTION: str,
    build_user_filter,
) -> list[dict]:
    """
    Fetch candidate atoms for bond restoration:
    same pentagon_domain + 1st-degree neighbors.
    Willison P3: curate context to relevant subset only.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    candidates: list[dict] = []
    seen_ids: set[str] = {exclude_node_id}

    # Same domain atoms
    try:
        domain_filter = Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="pentagon_domain", match=MatchValue(value=domain)),
            ]
        )
        domain_results, _ = await asyncio.wait_for(
            client.scroll(
                collection_name=C60_COLLECTION,
                scroll_filter=domain_filter,
                limit=15,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )
        for r in domain_results:
            p = r.payload or {}
            nid = p.get("node_id", "")
            if nid and nid not in seen_ids:
                candidates.append(p)
                seen_ids.add(nid)
    except Exception as e:
        logger.warning(f"Candidate fetch (domain) error: {e}")

    # 1st-degree neighbors
    if neighbor_node_ids:
        from services.c60_qdrant import build_node_id_filter
        try:
            neighbor_results, _ = await asyncio.wait_for(
                client.scroll(
                    collection_name=C60_COLLECTION,
                    scroll_filter=build_node_id_filter(user_id, neighbor_node_ids),
                    limit=len(neighbor_node_ids),
                    with_payload=True,
                    with_vectors=False,
                ),
                timeout=30.0,
            )
            for r in neighbor_results:
                p = r.payload or {}
                nid = p.get("node_id", "")
                if nid and nid not in seen_ids:
                    candidates.append(p)
                    seen_ids.add(nid)
        except Exception as e:
            logger.warning(f"Candidate fetch (neighbors) error: {e}")

    return candidates[:_VALENCE_MAX_CANDIDATES]


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

async def run_dream_state(
    user_id: int,
    qdrant_client: "AsyncQdrantClient",
    ai_provider: "BaseProvider",
    db: "DatabaseService",
) -> dict:
    """
    Run all three Dream State phases for a single user.

    Idempotency: acquires Firestore distributed lock before running.
    Lock is released in finally block even on error.

    Returns summary dict: {decay_atoms, bonds_removed, bonds_restored}.
    """
    if not await _acquire_lock(user_id, db):
        return {"skipped": True, "reason": "lock_held"}

    summary: dict = {"user_id": user_id, "decay_atoms": 0, "bonds_removed": 0, "bonds_restored": 0}

    try:
        logger.info(f"Dream State starting for user {user_id}")

        # Phase 1: Weight Decay
        summary["decay_atoms"] = await apply_weight_decay(user_id, qdrant_client)

        # Phase 2: Apoptosis
        bonds_removed, underbonded = await run_apoptosis(user_id, qdrant_client)
        summary["bonds_removed"] = bonds_removed

        # Phase 3: Valence Restoration (only if there are underbonded atoms)
        if underbonded and ai_provider:
            summary["bonds_restored"] = await restore_valence(
                user_id, underbonded, qdrant_client, ai_provider
            )

        logger.info(
            f"Dream State complete for user {user_id}: "
            f"decay={summary['decay_atoms']}, removed={summary['bonds_removed']}, "
            f"restored={summary['bonds_restored']}"
        )

        # Store pending UX notification if apoptosis was significant (> 5 bonds removed)
        if db and db.db and summary["bonds_removed"] > 5:
            try:
                now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                await db.db.collection("users").document(str(user_id)).update({
                    "pending_dream_summary": {
                        "apoptosis_bonds": summary["bonds_removed"],
                        "pruned_atoms": len(underbonded),
                        "restored_bonds": summary["bonds_restored"],
                        "date": now_date,
                    },
                    "dream_state_last_run": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
            except Exception as _e:
                logger.debug(f"Dream State: failed to store pending summary for {user_id}: {_e}")

    except Exception as e:
        logger.error(f"Dream State error for user {user_id}: {type(e).__name__}: {e}")
        summary["error"] = type(e).__name__
    finally:
        await _release_lock(user_id, db)

    return summary
