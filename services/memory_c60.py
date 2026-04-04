"""
C60 Fullerene Memory Service — drop-in replacement for MemoryService.

Fowler × Brandur × Backend — Drop-in interface (P6) + Batch writes + Resource management.

Public API is identical to MemoryService (7 methods), so telegram_bot.py requires zero changes.

Architecture:
- store_* → async queue → STORE_ATOM task → C60 Atom Processor → Qdrant upsert
- search_memory → vector search (top-5) + bond traversal → dedup → return
- Dream State integration via bond weight updates
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from services.db import DatabaseService

# ──────────────────────────────────────────────────────────────────────────────
# Task 7: Typed MemoryTask envelope
# Fowler × Backend — Separate task envelope (P3) + Async correctness (P1, P2)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MemoryTask:
    """Typed queue envelope for memory operations."""
    kind: Literal["STORE_ATOM", "UPDATE_BOND_ACTIVATION"]
    user_id: int
    # STORE_ATOM fields
    text: str | None = None
    role: str | None = None
    # UPDATE_BOND_ACTIVATION fields
    node_id: str | None = None
    bond_target_nodes: list[str] = field(default_factory=list)


# module-level set to prevent GC of fire-and-forget tasks (Backend P3)
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> asyncio.Task:
    """Schedule a coroutine as a non-awaited background task with GC protection."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(
        lambda t: (
            _background_tasks.discard(t),
            logger.error(f"Background task error: {t.exception()}") if t.exception() else None,
        )
    )
    return task


# ──────────────────────────────────────────────────────────────────────────────
# MemoryC60Service
# ──────────────────────────────────────────────────────────────────────────────

_MEM0_AVAILABLE = False
try:
    from mem0 import AsyncMemory
    _MEM0_AVAILABLE = True
except ImportError:
    pass

_QDRANT_AVAILABLE = False
try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import PointStruct
    _QDRANT_AVAILABLE = True
except ImportError:
    logger.warning("qdrant-client not installed. pip install qdrant-client")

_GENAI_AVAILABLE = False
try:
    from google import genai as _genai
    _GENAI_AVAILABLE = True
except ImportError:
    logger.warning("google-genai not installed.")


class MemoryC60Service:
    """
    C60 Fullerene Memory — drop-in for MemoryService.

    Same 7 public methods. Backed by Qdrant vector store with
    graph-structured payloads (C60 topology).
    """

    def __init__(
        self,
        gemini_api_key: str,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        db: "DatabaseService | None" = None,
    ):
        self._gemini_api_key = gemini_api_key
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key
        self._db = db

        self._qdrant: "AsyncQdrantClient | None" = None
        self._embed_client = None
        self._c60_provider = None

        # Async queue for fire-and-forget writes
        self._queue: asyncio.Queue[MemoryTask] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        logger.info("MemoryC60Service initialised (lazy Qdrant connection).")

    # ── Lazy initialisation ──

    async def _ensure_qdrant(self) -> "AsyncQdrantClient":
        """Lazy Qdrant client init + collection bootstrap."""
        if self._qdrant is not None:
            return self._qdrant

        if not _QDRANT_AVAILABLE:
            raise ImportError("qdrant-client not installed. pip install qdrant-client")

        from services.c60_qdrant import ensure_collection

        kwargs: dict = {}
        if self._qdrant_api_key:
            kwargs["api_key"] = self._qdrant_api_key

        if self._qdrant_url:
            self._qdrant = AsyncQdrantClient(url=self._qdrant_url, **kwargs)
        else:
            # Local Qdrant for development
            self._qdrant = AsyncQdrantClient(path="/tmp/qdrant_c60")
            logger.warning(
                "QDRANT_URL not set — using local Qdrant (/tmp/qdrant_c60). "
                "Data WILL NOT persist on Cloud Run restart."
            )

        await ensure_collection(self._qdrant)
        return self._qdrant

    async def _ensure_embed_client(self):
        """Lazy Gemini embedding client init."""
        if self._embed_client is not None:
            return self._embed_client
        if not _GENAI_AVAILABLE:
            raise ImportError("google-genai not installed.")
        self._embed_client = _genai.Client(api_key=self._gemini_api_key)
        return self._embed_client

    async def _ensure_c60_provider(self):
        """Lazy C60 Topology Engine provider (dedicated Gemini instance)."""
        if self._c60_provider is not None:
            return self._c60_provider
        from services.ai_engine import create_provider
        self._c60_provider = create_provider("gemini", "gemini-2.0-flash")
        return self._c60_provider

    # ── Embedding ──

    async def _embed(self, text: str) -> list[float] | None:
        """Generate 768-dim embedding via Gemini embedding-001."""
        try:
            client = await self._ensure_embed_client()
            result = await asyncio.wait_for(
                client.aio.models.embed_content(
                    model="models/gemini-embedding-001",
                    contents=text,
                ),
                timeout=30.0,
            )
            return list(result.embeddings[0].values)
        except asyncio.TimeoutError:
            logger.error("Embedding API timeout")
            return None
        except Exception as e:
            logger.error(f"Embedding error: {type(e).__name__}: {e}")
            return None

    # ── Atom count ──

    async def _count_atoms(self, user_id: int) -> int:
        """Count atoms for user in Qdrant. Returns 0 on error."""
        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, build_user_filter
            result = await client.count(
                collection_name=C60_COLLECTION,
                count_filter=build_user_filter(user_id),
                exact=False,  # approximate is fine for this purpose
            )
            return result.count
        except Exception as e:
            logger.error(f"count_atoms error for {user_id}: {e}")
            return 0

    # ── Qdrant upsert ──

    async def _upsert_atom(
        self,
        user_id: int,
        atom,  # C60Atom
        vector: list[float],
        role: str = "user",
    ) -> None:
        """Write a C60Atom to Qdrant."""
        from services.c60_qdrant import C60_COLLECTION, node_id_to_qdrant_uuid

        payload = {
            "user_id": str(user_id),
            "node_id": atom.node_id,
            "pentagon_domain": atom.pentagon_domain,
            "vector_core": atom.vector_core,
            "covalent_bonds": [
                {
                    "target_node": b.target_node,
                    "relation_type": b.relation_type,
                    "weight": b.weight,
                    "last_activated": b.last_activated.isoformat().replace("+00:00", "Z"),
                }
                for b in atom.covalent_bonds
            ],
            "is_shadow": atom.is_shadow,
            "created_at": atom.created_at.isoformat().replace("+00:00", "Z"),
            "role": role,
        }

        point = PointStruct(
            id=node_id_to_qdrant_uuid(atom.node_id),
            vector=vector,
            payload=payload,
        )

        client = await self._ensure_qdrant()
        await asyncio.wait_for(
            client.upsert(collection_name=C60_COLLECTION, points=[point]),
            timeout=30.0,
        )
        logger.info(
            f"C60 atom upserted: user={user_id}, domain={atom.pentagon_domain!r}, "
            f"node_id={atom.node_id[:8]}…"
        )

    async def _upsert_plain_atom(
        self,
        user_id: int,
        text: str,
        vector: list[float],
        role: str = "user",
    ) -> None:
        """
        Fallback: store a plain-text memory when C60 crystallization fails.
        EC-12: text not logged — only len.
        """
        from services.c60_qdrant import C60_COLLECTION, node_id_to_qdrant_uuid

        node_id = str(uuid.uuid4())
        now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        payload = {
            "user_id": str(user_id),
            "node_id": node_id,
            "pentagon_domain": "Общее",
            "vector_core": text[:500],  # truncate for storage
            "covalent_bonds": [],
            "is_shadow": False,
            "created_at": now_z,
            "role": role,
        }

        point = PointStruct(
            id=node_id_to_qdrant_uuid(node_id),
            vector=vector,
            payload=payload,
        )

        client = await self._ensure_qdrant()
        await asyncio.wait_for(
            client.upsert(collection_name=C60_COLLECTION, points=[point]),
            timeout=30.0,
        )
        logger.info(
            f"Plain-text fallback atom stored: user={user_id}, text_len={len(text)}"
        )

    # ── Domain Discovery trigger ──

    def _maybe_trigger_domain_discovery(self, user_id: int, atom_count: int) -> None:
        """Fire-and-forget domain discovery if threshold reached."""
        if self._db is None or atom_count < 10:
            return

        async def _run():
            from services.domain_discovery import discover_domains
            try:
                provider = await self._ensure_c60_provider()
                await discover_domains(user_id, atom_count, provider, self._db)
            except Exception as e:
                logger.error(f"Domain Discovery background error for {user_id}: {e}")

        _fire_and_forget(_run())

    # ── Task 7: Queue worker ──

    async def _start_worker(self) -> None:
        """Start queue worker if not running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._queue_worker())

    async def _queue_worker(self) -> None:
        """
        Process memory tasks from the queue.

        - task_done() always in finally (Task 7)
        - Operational errors (TimeoutError, HTTP 429/500) → retry once with backoff
        - Programmer errors (TypeError, KeyError, AttributeError) → log + discard
        """
        while True:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=300)
            except asyncio.TimeoutError:
                logger.info("MemoryC60 queue worker: idle 5 min, shutting down")
                break

            try:
                await self._process_task(task)
            except (TypeError, KeyError, AttributeError, ValueError) as e:
                # Programmer error — discard, do not retry
                logger.error(
                    f"MemoryC60 worker programmer error (discarding): "
                    f"{type(e).__name__}: {e}, task.kind={task.kind}, user={task.user_id}"
                )
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                # Operational error — retry once with backoff
                logger.warning(
                    f"MemoryC60 worker operational error (retrying): "
                    f"{type(e).__name__}: {e}, task.kind={task.kind}, user={task.user_id}"
                )
                await asyncio.sleep(2.0)
                try:
                    await self._process_task(task)
                except Exception as retry_e:
                    logger.error(
                        f"MemoryC60 worker retry failed: "
                        f"{type(retry_e).__name__}: {retry_e}, task.kind={task.kind}"
                    )
            except Exception as e:
                logger.error(
                    f"MemoryC60 worker unexpected error: "
                    f"{type(e).__name__}: {e}, task.kind={task.kind}, user={task.user_id}"
                )
            finally:
                self._queue.task_done()

    async def _process_task(self, task: MemoryTask) -> None:
        """Dispatch task to appropriate handler."""
        if task.kind == "STORE_ATOM":
            await self._do_store_atom(task)
        elif task.kind == "UPDATE_BOND_ACTIVATION":
            await self._do_update_bond_activation(task)
        else:
            logger.error(f"MemoryC60: unknown task kind: {task.kind!r}")

    async def _do_store_atom(self, task: MemoryTask) -> None:
        """
        Crystallize text into C60Atom → embed → upsert to Qdrant.
        Falls back to plain-text atom if crystallization fails.
        """
        text = task.text or ""
        if not text.strip():
            return

        # Get user domains (empty list = use default domains in prompt)
        domains: list[str] = []
        if self._db:
            from services.domain_discovery import get_user_domains
            try:
                domains = await get_user_domains(task.user_id, self._db)
            except Exception as e:
                logger.warning(f"Could not load domains for {task.user_id}: {e}")

        # Try C60 crystallization
        atom = None
        try:
            provider = await self._ensure_c60_provider()
            from services.c60_atom_processor import process_message
            atom = await process_message(text, domains, provider)
        except Exception as e:
            logger.warning(f"C60 crystallization failed for {task.user_id}: {type(e).__name__}: {e}")

        # Generate embedding (on vector_core if atom, else raw text)
        embed_text = atom.vector_core if atom else text[:500]
        vector = await self._embed(embed_text)

        if vector is None:
            logger.warning(f"Embedding failed for {task.user_id} — skipping store")
            return

        if atom:
            await self._upsert_atom(task.user_id, atom, vector, role=task.role or "user")
        else:
            await self._upsert_plain_atom(task.user_id, text, vector, role=task.role or "user")

        # Trigger domain discovery (fire-and-forget, non-blocking)
        atom_count = await self._count_atoms(task.user_id)
        self._maybe_trigger_domain_discovery(task.user_id, atom_count)

    async def _do_update_bond_activation(self, task: MemoryTask) -> None:
        """Update last_activated for traversed bonds in Qdrant payload."""
        if not task.bond_target_nodes:
            return

        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, node_id_to_qdrant_uuid

            point_id = node_id_to_qdrant_uuid(task.node_id)
            now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            # Fetch current payload
            results = await asyncio.wait_for(
                client.retrieve(
                    collection_name=C60_COLLECTION,
                    ids=[point_id],
                    with_payload=True,
                ),
                timeout=30.0,
            )

            if not results:
                return

            payload = results[0].payload or {}
            bonds = payload.get("covalent_bonds", [])

            updated = False
            for bond in bonds:
                if bond.get("target_node") in task.bond_target_nodes:
                    bond["last_activated"] = now_z
                    updated = True

            if updated:
                await asyncio.wait_for(
                    client.set_payload(
                        collection_name=C60_COLLECTION,
                        payload={"covalent_bonds": bonds},
                        points=[point_id],
                    ),
                    timeout=30.0,
                )
        except Exception as e:
            logger.warning(f"Bond activation update error for {task.user_id}: {e}")

    # ── Recent atoms for context ──

    async def _get_recent_atoms(self, user_id: int, limit: int = 5) -> list[dict]:
        """Fetch recent atoms from Qdrant for bond context."""
        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, build_user_filter

            results, _ = await asyncio.wait_for(
                client.scroll(
                    collection_name=C60_COLLECTION,
                    scroll_filter=build_user_filter(user_id),
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                ),
                timeout=30.0,
            )
            return [r.payload for r in results if r.payload]
        except Exception as e:
            logger.warning(f"Could not fetch recent atoms for {user_id}: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════════
    # Public API — identical to MemoryService
    # ══════════════════════════════════════════════════════════════════════════

    async def store_message(
        self, user_id: int, role: str, content: str, infer: bool = True
    ) -> None:
        """Queue a single message for C60 crystallization. EC-12: content not logged."""
        task = MemoryTask(kind="STORE_ATOM", user_id=user_id, text=content, role=role)
        await self._queue.put(task)
        await self._start_worker()
        logger.info(
            f"C60 queued: user={user_id}, role={role}, text_len={len(content)}, "
            f"queue_size={self._queue.qsize()}"
        )

    async def store_conversation(
        self, user_id: int, user_text: str, assistant_text: str
    ) -> None:
        """Queue both sides of a conversation turn for C60 crystallization."""
        combined = f"[User]: {user_text}\n[Assistant]: {assistant_text}"
        task = MemoryTask(kind="STORE_ATOM", user_id=user_id, text=combined, role="user")
        await self._queue.put(task)
        await self._start_worker()
        logger.info(
            f"C60 conversation queued: user={user_id}, "
            f"text_len={len(combined)}, queue_size={self._queue.qsize()}"
        )

    async def store_bulk(self, user_id: int, content: str) -> None:
        """Queue bulk content for C60 crystallization."""
        task = MemoryTask(kind="STORE_ATOM", user_id=user_id, text=content, role="user")
        await self._queue.put(task)
        await self._start_worker()
        logger.info(
            f"C60 bulk queued: user={user_id}, "
            f"text_len={len(content)}, queue_size={self._queue.qsize()}"
        )

    async def search_memory(
        self, user_id: int, query: str, limit: int = 5
    ) -> list[dict]:
        """
        Hybrid search: vector top-5 + bond traversal.

        Returns list of {id, content, score, role, domain}.
        EC-12: query not logged.

        Task 9: Backend × Brandur — Hot path async safety.
        """
        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, build_user_filter, build_node_id_filter

            # 1. Embed query
            query_vector = await self._embed(query)
            if query_vector is None:
                return []

            # 2. Vector search top-5
            search_results = await asyncio.wait_for(
                client.search(
                    collection_name=C60_COLLECTION,
                    query_vector=query_vector,
                    query_filter=build_user_filter(user_id),
                    limit=5,
                    with_payload=True,
                ),
                timeout=30.0,
            )

            if not search_results:
                return []

            direct_hits = []
            bond_target_node_ids: list[str] = []
            # Track which source atom references which targets (for activation update)
            activation_map: dict[str, list[str]] = {}

            for hit in search_results:
                p = hit.payload or {}
                direct_hits.append({
                    "id": p.get("node_id", ""),
                    "content": p.get("vector_core", ""),
                    "score": float(hit.score),
                    "role": p.get("role", "memory"),
                    "domain": p.get("pentagon_domain", ""),
                    "_is_bond_neighbor": False,
                })

                # Collect bond targets for traversal
                for bond in p.get("covalent_bonds", []):
                    tn = bond.get("target_node", "")
                    if tn:
                        bond_target_node_ids.append(tn)
                        activation_map.setdefault(p.get("node_id", ""), []).append(tn)

            # 3. Fetch bond neighbors
            bond_hits: list[dict] = []
            if bond_target_node_ids:
                unique_targets = list(dict.fromkeys(bond_target_node_ids))[:20]
                try:
                    neighbor_results, _ = await asyncio.wait_for(
                        client.scroll(
                            collection_name=C60_COLLECTION,
                            scroll_filter=build_node_id_filter(user_id, unique_targets),
                            limit=len(unique_targets),
                            with_payload=True,
                            with_vectors=False,
                        ),
                        timeout=30.0,
                    )
                    for r in neighbor_results:
                        p = r.payload or {}
                        bond_hits.append({
                            "id": p.get("node_id", ""),
                            "content": p.get("vector_core", ""),
                            "score": 0.0,  # bond neighbors have no direct score
                            "role": p.get("role", "memory"),
                            "domain": p.get("pentagon_domain", ""),
                            "_is_bond_neighbor": True,
                        })
                except Exception as e:
                    logger.warning(f"Bond traversal error for {user_id}: {e}")

            # 4. Deduplicate: direct hits take priority
            seen_ids = {h["id"] for h in direct_hits}
            for bh in bond_hits:
                if bh["id"] not in seen_ids:
                    seen_ids.add(bh["id"])
                    direct_hits.append(bh)

            # 5. Update bond activation timestamps (non-blocking, fire-and-forget)
            for source_node_id, targets in activation_map.items():
                if targets:
                    act_task = MemoryTask(
                        kind="UPDATE_BOND_ACTIVATION",
                        user_id=user_id,
                        node_id=source_node_id,
                        bond_target_nodes=targets,
                    )
                    await self._queue.put(act_task)
                    await self._start_worker()

            # Return up to `limit` results, dropping internal _is_bond_neighbor flag
            return [
                {k: v for k, v in h.items() if not k.startswith("_")}
                for h in direct_hits[:limit]
            ]

        except Exception as e:
            logger.error(f"search_memory error for {user_id}: {type(e).__name__}: {e}")
            return []

    async def get_memory_context(self, user_id: int, user_text: str) -> str:
        """
        Returns formatted memory context for LLM injection.
        Direct hits first, bond-neighbors second. Limit 2000 chars.
        EC-12: user_text not logged.
        """
        results = await self.search_memory(user_id, user_text, limit=15)
        if not results:
            return ""

        relevant = [r for r in results if r.get("score", 0) >= 0.2 or r.get("score", 0) == 0.0]
        # 0.0 score = bond neighbors, always included if they exist
        # Only filter out low-score direct hits
        filtered = []
        for r in results:
            score = r.get("score", 0.0)
            if score == 0.0 or score >= 0.2:  # bond neighbor or relevant hit
                filtered.append(r)

        if not filtered:
            return ""

        lines = ["[КОНТЕКСТ ИЗ ДОЛГОВРЕМЕННОЙ ПАМЯТИ — C60 атомы:]"]
        char_budget = 1900  # leave room for headers

        for i, r in enumerate(filtered, 1):
            content = r.get("content", "")[:300]
            domain = r.get("domain", "")
            entry = f"  {i}. [{domain}] {content}" if domain else f"  {i}. {content}"

            if len("\n".join(lines)) + len(entry) > char_budget:
                break
            lines.append(entry)

        lines.append("[КОНЕЦ КОНТЕКСТА ПАМЯТИ]\n")
        context = "\n".join(lines)

        logger.info(
            f"C60 memory context: user={user_id}, atoms={len(filtered)}, chars={len(context)}"
        )
        return context

    async def get_all_memories(self, user_id: int, limit: int = 100) -> list[dict]:
        """Retrieve all atoms for user via Qdrant scroll."""
        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, build_user_filter

            results, _ = await asyncio.wait_for(
                client.scroll(
                    collection_name=C60_COLLECTION,
                    scroll_filter=build_user_filter(user_id),
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                ),
                timeout=30.0,
            )
            return [r.payload for r in results if r.payload]
        except Exception as e:
            logger.error(f"get_all_memories error for {user_id}: {e}")
            return []

    async def delete_all(self, user_id: int) -> None:
        """Delete all atoms for user from Qdrant."""
        try:
            client = await self._ensure_qdrant()
            from services.c60_qdrant import C60_COLLECTION, build_user_filter

            await asyncio.wait_for(
                client.delete(
                    collection_name=C60_COLLECTION,
                    points_selector=build_user_filter(user_id),
                ),
                timeout=30.0,
            )
            logger.info(f"C60 delete_all: all atoms deleted for user {user_id}")
        except Exception as e:
            logger.error(f"delete_all error for {user_id}: {e}")

    async def summarize_old_messages(
        self, user_id: int, ai_engine, batch_size: int = 30
    ) -> None:
        """
        No-op — C60 handles deduplication automatically via bond decay.
        Kept for backward compatibility with cron endpoint.
        """
        logger.info(f"summarize_old_messages: no-op for C60 (user {user_id})")

    async def close(self) -> None:
        """Drain queue and close Qdrant client. Called from FastAPI lifespan shutdown."""
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._queue.join(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("C60 memory queue not drained in 60s")
            self._worker_task.cancel()

        if self._qdrant:
            await self._qdrant.close()

        logger.info("MemoryC60Service closed.")

    @property
    def memory(self):
        """Compat property (MemoryService had this for Mem0 access)."""
        return None
