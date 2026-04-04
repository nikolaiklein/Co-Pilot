"""
Domain Discovery pipeline — generates and maintains a user's personal pentagon domains.

Fowler × Backend × Willison — Architecture earns its boundaries (P6) + Domain idempotency.

Each user has 12 personal domains generated from their first conversations.
Domains evolve as more atoms accumulate, but only when change is significant.

Public API:
    discover_domains(user_id, atom_count, ai_provider, db) -> list[str] | None

EC-4: logs with step_label="domain_discovery".
EC-9: domain names validated via allowlist-regex before Firestore write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.ai_engine import BaseProvider
    from services.db import DatabaseService

logger = logging.getLogger(__name__)

# Trigger thresholds
_DISCOVERY_MIN_ATOMS = 10       # run domain discovery only after this many atoms
_REFRESH_MIN_NEW_ATOMS = 30     # refresh threshold: 30 new atoms since last discovery
_REFRESH_MIN_DIFF = 2           # refresh only if > 2 domains would change

# Validation
_DOMAIN_RE = re.compile(r"^[А-Яа-яёЁA-Za-z0-9 \-_()/]+$")

# Firestore collection for pentagon domains
_DOMAINS_COLLECTION = "pentagon_domains"

_DISCOVERY_PROMPT = """Ты — эксперт по психологии личности и биографическому анализу.

На основе кратких фактов о пользователе создай 12 персональных жизненных доменов
для системы долговременной памяти. Каждый домен — важная сфера жизни именно этого человека.

## Факты о пользователе
{facts}

## Требования к доменам
- Ровно 12 доменов
- Каждый домен — краткое название (до 40 символов)
- Только кириллица, латиница, цифры, пробелы и дефисы
- Домены должны отражать ЭТОГО конкретного человека, а не универсальный шаблон
- Примеры (но не шаблон!): "Технологии и инновации", "Семья и отношения",
  "Финансы и инвестиции", "Духовное развитие", "Здоровье и энергия"

## Формат ответа (только JSON, без блока кода)
{{
  "domains": [
    "Домен 1",
    "Домен 2",
    "...",
    "Домен 12"
  ]
}}
"""

_EVOLUTION_PROMPT = """Ты — эксперт по психологии личности и биографическому анализу.

Существующие домены пользователя уже созданы, но накопились новые данные.
Оцени, нужно ли обновить домены на основе новых фактов.

## Текущие домены
{current_domains}

## Новые факты о пользователе
{new_facts}

## Задача
Если новые факты открывают важные сферы жизни, не охваченные текущими доменами,
предложи обновлённый список (ровно 12 доменов). Если домены актуальны — верни текущие.

## Формат ответа (только JSON, без блока кода)
{{
  "domains": [
    "Домен 1",
    ...
    "Домен 12"
  ]
}}
"""


def _validate_domains(domains: list[str]) -> list[str] | None:
    """
    Validate and clean domain names.
    Returns cleaned list or None if too many fail validation.

    EC-9: domains validated via allowlist-regex before Firestore write.
    """
    valid = []
    for d in domains:
        if not isinstance(d, str):
            continue
        d = d.strip()[:40]
        if _DOMAIN_RE.match(d):
            valid.append(d)
        else:
            logger.warning(f"Domain Discovery: domain failed validation — {d!r}")

    if len(valid) < 10:
        logger.warning(f"Domain Discovery: too few valid domains ({len(valid)}/12)")
        return None
    return valid[:12]


def _count_domain_changes(old: list[str], new: list[str]) -> int:
    """Count domains that changed between two lists."""
    old_set = set(old)
    new_set = set(new)
    return len(old_set.symmetric_difference(new_set))


async def _load_current_domains(user_id: int, db: "DatabaseService") -> dict | None:
    """Load current pentagon_domains document for user, or None."""
    try:
        doc_ref = db.db.collection(_DOMAINS_COLLECTION).document(str(user_id))
        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.error(f"Domain Discovery: failed to load domains for {user_id}: {e}")
        return None


async def _get_user_facts(user_id: int, db: "DatabaseService") -> str:
    """
    Build a compact facts string from the user's profile summary.
    EC-12: only uses profile metadata, not raw message content.
    """
    try:
        user_doc = await db.db.collection("users").document(str(user_id)).get()
        if not user_doc.exists:
            return "Нет данных"

        data = user_doc.to_dict() or {}
        profile = data.get("profile_summary", {}) or {}

        facts = []
        if profile.get("summary"):
            facts.append(f"Портрет: {profile['summary'][:200]}")
        if profile.get("interests"):
            facts.append(f"Интересы: {', '.join(profile['interests'][:10])}")
        if profile.get("new_skills"):
            facts.append(f"Навыки: {', '.join(profile['new_skills'][:10])}")
        if profile.get("dreams"):
            facts.append(f"Мечты: {', '.join(profile['dreams'][:5])}")
        if profile.get("pain_points"):
            facts.append(f"Боли: {', '.join(profile['pain_points'][:5])}")

        return "\n".join(facts) if facts else "Профиль в процессе формирования"
    except Exception as e:
        logger.error(f"Domain Discovery: failed to get facts for {user_id}: {e}")
        return "Нет данных"


async def discover_domains(
    user_id: int,
    atom_count: int,
    ai_provider: "BaseProvider",
    db: "DatabaseService",
) -> list[str] | None:
    """
    Generate or refresh the user's 12 personal pentagon domains.

    Idempotent: checks Firestore before calling LLM.
    Returns the domains list or None on failure.

    Trigger: called after atom_count reaches _DISCOVERY_MIN_ATOMS.
    Refresh: only if atom_count grew by _REFRESH_MIN_NEW_ATOMS and domains changed significantly.

    EC-4: logs with step_label="domain_discovery".
    """
    if atom_count < _DISCOVERY_MIN_ATOMS:
        logger.debug(
            f"Domain Discovery: skipped for user {user_id} — "
            f"atom_count={atom_count} < {_DISCOVERY_MIN_ATOMS}"
        )
        return None

    current = await _load_current_domains(user_id, db)

    if current:
        current_version = current.get("version", 1)
        current_domains = current.get("domains", [])

        # Check if refresh is needed
        last_atom_count = current.get("atom_count_at_discovery", _DISCOVERY_MIN_ATOMS)
        if atom_count - last_atom_count < _REFRESH_MIN_NEW_ATOMS:
            logger.debug(
                f"Domain Discovery: using cached domains for user {user_id} "
                f"(atom_count={atom_count}, last_discovery={last_atom_count})"
            )
            return current_domains
    else:
        current_version = 0
        current_domains = []

    # Build LLM prompt
    facts = await _get_user_facts(user_id, db)

    if current_domains:
        prompt_text = _EVOLUTION_PROMPT.format(
            current_domains="\n".join(f"- {d}" for d in current_domains),
            new_facts=facts,
        )
    else:
        prompt_text = _DISCOVERY_PROMPT.format(facts=facts)

    model_name = getattr(ai_provider, "model", "unknown")
    logger.info(
        f"Domain Discovery: calling LLM for user {user_id}, "
        f"model={model_name}, step_label=domain_discovery"
    )

    start_time = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            ai_provider.generate(
                [{"role": "user", "content": prompt_text}],
                "Отвечай только JSON, без пояснений.",
                temperature=0.2,
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"Domain Discovery: LLM timeout for user {user_id}, step_label=domain_discovery")
        return current_domains or None
    except Exception as e:
        logger.error(
            f"Domain Discovery: LLM error for user {user_id}: {type(e).__name__}, "
            f"step_label=domain_discovery"
        )
        return current_domains or None

    latency_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        f"Domain Discovery: LLM response latency={latency_ms}ms, "
        f"step_label=domain_discovery"
    )

    # Parse JSON
    json_text = raw.strip()
    if json_text.startswith("```"):
        json_text = "\n".join(
            line for line in json_text.splitlines() if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(json_text)
        new_domains_raw = data.get("domains", [])
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Domain Discovery: JSON parse failed for user {user_id}: {e}, step_label=domain_discovery")
        return current_domains or None

    # EC-9: validate domain names
    new_domains = _validate_domains(new_domains_raw)
    if not new_domains:
        logger.warning(f"Domain Discovery: validation failed for user {user_id}, step_label=domain_discovery")
        return current_domains or None

    # Check if change is significant enough to save
    if current_domains and _count_domain_changes(current_domains, new_domains) <= _REFRESH_MIN_DIFF:
        logger.info(
            f"Domain Discovery: no significant change for user {user_id} "
            f"({_count_domain_changes(current_domains, new_domains)} domains changed), "
            f"keeping existing, step_label=domain_discovery"
        )
        return current_domains

    # Save to Firestore
    from google.cloud import firestore as _fs
    doc_ref = db.db.collection(_DOMAINS_COLLECTION).document(str(user_id))
    new_version = current_version + 1
    data_to_save = {
        "domains": new_domains,
        "version": new_version,
        "user_id": user_id,
        "atom_count_at_discovery": atom_count,
        "updated_at": _fs.SERVER_TIMESTAMP,
    }

    try:
        if current_version == 0:
            await doc_ref.create(data_to_save)
        else:
            await doc_ref.set(data_to_save)
        logger.info(
            f"Domain Discovery: saved {len(new_domains)} domains for user {user_id}, "
            f"version={new_version}, step_label=domain_discovery"
        )
    except Exception as e:
        logger.error(f"Domain Discovery: Firestore save failed for {user_id}: {e}")
        return current_domains or None

    return new_domains


async def get_user_domains(user_id: int, db: "DatabaseService") -> list[str]:
    """
    Returns the user's current pentagon domains from Firestore.
    Returns empty list if not yet discovered.
    """
    current = await _load_current_domains(user_id, db)
    if current:
        return current.get("domains", [])
    return []
