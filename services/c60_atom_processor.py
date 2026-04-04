"""
C60 Topology Engine — crystallizes user messages into C60Atoms.

Willison × Fowler — Principle 1 (Structured output) + Extract pure logic.

Public API:
    process_message(text, user_domains, ai_provider, system_prompt=None) -> C60Atom | None

EC-4: logs every LLM call with step_label, model, latency, finish_reason.
EC-12: never logs message text — only len(text).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

from services.c60_models import C60Atom, _DOMAIN_RE

if TYPE_CHECKING:
    from services.ai_engine import BaseProvider

logger = logging.getLogger(__name__)

# Vault item ID where the master prompt is stored (owner: user 292628110)
C60_VAULT_PROMPT_ID = "bZ3rtO8Ms8ixb8U4cnyu"
VAULT_OWNER_USER_ID = 292628110

# LLM timeout per call (seconds)
_LLM_TIMEOUT_S = 30.0

# ──────────────────────────────────────────────────────────────────────────────
# Embedded default C60 Topology Engine prompt
# (used as fallback when vault is unavailable)
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = """Ты — C60 Topology Engine, ядро кристаллизации Фуллереновой Памяти.

Твоя задача: преобразовать одно сообщение пользователя в структурированный атом памяти формата C60.

Атом памяти — это не пересказ, а кристаллизованная суть. Дистиллируй смысл.

## Домены пользователя (pentagon)
{domains}

## Правила кристаллизации

1. **pentagon_domain** — выбери ОДИН наиболее подходящий домен из списка выше.
2. **vector_core** — ключевая фраза для семантического поиска (не менее 10 символов, суть памяти).
3. **covalent_bonds** — РОВНО 3 семантические связи. Не больше, не меньше.
   - **target_node**: смысловой якорь связанной концепции (строка, не UUID)
   - **relation_type**: одно из РАЗВИВАЕТ | ПРОТИВОРЕЧИТ | ВЛИЯЕТ_НА | ЧАСТЬ_ОТ
   - **weight**: сила связи от 0.3 до 1.0
   - **last_activated**: текущее время в UTC ISO8601 с Z

## КРИТИЧНО
- covalent_bonds ДОЛЖЕН содержать РОВНО 3 элемента.
- Не добавляй лишних полей.
- Временные метки только в формате: 2026-04-03T12:00:00Z

## Формат ответа (только JSON, без блока кода)
{
  "pentagon_domain": "<один из доменов выше>",
  "vector_core": "<суть памяти для векторного поиска>",
  "covalent_bonds": [
    {
      "target_node": "<смысловой якорь связи 1>",
      "relation_type": "РАЗВИВАЕТ",
      "weight": 0.8,
      "last_activated": "2026-04-03T12:00:00Z"
    },
    {
      "target_node": "<смысловой якорь связи 2>",
      "relation_type": "ВЛИЯЕТ_НА",
      "weight": 0.6,
      "last_activated": "2026-04-03T12:00:00Z"
    },
    {
      "target_node": "<смысловой якорь связи 3>",
      "relation_type": "ЧАСТЬ_ОТ",
      "weight": 0.5,
      "last_activated": "2026-04-03T12:00:00Z"
    }
  ]
}
"""

_EVOLUTION_SYSTEM_PROMPT = """Ты — C60 Topology Engine, ядро кристаллизации Фуллереновой Памяти.

Твоя задача: преобразовать одно сообщение пользователя в структурированный атом памяти формата C60.

Атом памяти — это не пересказ, а кристаллизованная суть. Дистиллируй смысл.

## Домены пользователя (pentagon)
{domains}

## Существующие атомы в этом домене (для создания реальных связей)
{existing_atoms}

## Правила кристаллизации

1. **pentagon_domain** — выбери ОДИН наиболее подходящий домен из списка выше.
2. **vector_core** — ключевая фраза для семантического поиска (не менее 10 символов, суть памяти).
3. **covalent_bonds** — РОВНО 3 семантические связи. Предпочитай ссылаться на существующие атомы.
   - **target_node**: node_id существующего атома (из списка выше) ИЛИ новый смысловой якорь
   - **relation_type**: одно из РАЗВИВАЕТ | ПРОТИВОРЕЧИТ | ВЛИЯЕТ_НА | ЧАСТЬ_ОТ
   - **weight**: сила связи от 0.3 до 1.0
   - **last_activated**: текущее время в UTC ISO8601 с Z

## КРИТИЧНО
- covalent_bonds ДОЛЖЕН содержать РОВНО 3 элемента.
- Временные метки только в формате: 2026-04-03T12:00:00Z

## Формат ответа (только JSON, без блока кода)
{
  "pentagon_domain": "<один из доменов выше>",
  "vector_core": "<суть памяти для векторного поиска>",
  "covalent_bonds": [
    {
      "target_node": "<node_id или смысловой якорь>",
      "relation_type": "РАЗВИВАЕТ",
      "weight": 0.8,
      "last_activated": "2026-04-03T12:00:00Z"
    },
    {
      "target_node": "<node_id или смысловой якорь>",
      "relation_type": "ВЛИЯЕТ_НА",
      "weight": 0.6,
      "last_activated": "2026-04-03T12:00:00Z"
    },
    {
      "target_node": "<node_id или смысловой якорь>",
      "relation_type": "ЧАСТЬ_ОТ",
      "weight": 0.5,
      "last_activated": "2026-04-03T12:00:00Z"
    }
  ]
}
"""


def _build_system_prompt(
    user_domains: list[str],
    existing_atoms: list[dict] | None = None,
) -> str:
    """Returns the appropriate system prompt for this call."""
    domains_str = "\n".join(f"- {d}" for d in user_domains) if user_domains else "- Общее"

    if existing_atoms:
        atoms_str = "\n".join(
            f"- node_id: {a['node_id']} | domain: {a.get('pentagon_domain', '?')} | core: {a.get('vector_core', '')[:80]}"
            for a in existing_atoms[:20]  # limit context
        )
        return (
            _EVOLUTION_SYSTEM_PROMPT
            .replace("{domains}", domains_str)
            .replace("{existing_atoms}", atoms_str)
        )

    return _DEFAULT_SYSTEM_PROMPT.replace("{domains}", domains_str)


def _get_model_name(ai_provider: "BaseProvider") -> str:
    """Extract model name from provider for logging."""
    return getattr(ai_provider, "model", "unknown")


async def process_message(
    text: str,
    user_domains: list[str],
    ai_provider: "BaseProvider",
    existing_atoms: list[dict] | None = None,
    custom_system_prompt: str | None = None,
) -> C60Atom | None:
    """
    Crystallize a user message into a C60Atom via the Topology Engine.

    Args:
        text: User message to crystallize. EC-12: not logged — only len(text).
        user_domains: User's personal pentagon domains.
        ai_provider: AI provider instance (must support .generate()).
        existing_atoms: Optional list of recent atoms for bond context.
        custom_system_prompt: Override system prompt (e.g. from vault).

    Returns:
        C60Atom if successful, None on any failure (LLM error, parse error,
        validation error including bonds != 3).
    """
    model_name = _get_model_name(ai_provider)

    # EC-12: log only metadata, not content
    logger.info(
        f"C60 Topology Engine: text_len={len(text)}, domains={len(user_domains)}, "
        f"model={model_name}, step_label=c60_topology_engine"
    )

    system_prompt = custom_system_prompt or _build_system_prompt(user_domains, existing_atoms)
    messages = [{"role": "user", "content": text}]

    start_time = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            ai_provider.generate(messages, system_prompt, temperature=0.0),
            timeout=_LLM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"C60 Topology Engine timeout after {_LLM_TIMEOUT_S}s "
            f"(model={model_name}, step_label=c60_topology_engine)"
        )
        return None
    except Exception as e:
        logger.error(
            f"C60 Topology Engine LLM error: {type(e).__name__} "
            f"(model={model_name}, step_label=c60_topology_engine)"
        )
        return None

    latency_ms = int((time.monotonic() - start_time) * 1000)

    # EC-4: log call metadata (provider already logs tokens/finish_reason internally)
    logger.info(
        f"C60 Topology Engine response: latency={latency_ms}ms, "
        f"raw_len={len(raw)}, step_label=c60_topology_engine"
    )

    # Parse JSON — strip markdown code fences if present
    json_text = raw.strip()
    if json_text.startswith("```"):
        lines = json_text.splitlines()
        json_text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"C60 Topology Engine: JSON parse failed ({e}) — returning None "
            f"(model={model_name}, step_label=c60_topology_engine)"
        )
        return None

    # Inject code-generated fields (EC-9: schema validation before use)
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data["node_id"] = str(uuid.uuid4())
    data["created_at"] = now_utc
    data.setdefault("is_shadow", False)

    # Normalise bond last_activated timestamps
    for bond in data.get("covalent_bonds", []):
        bond.setdefault("last_activated", now_utc)

    # Brandur P1 fix #6: assert pentagon_domain is from the user's domain list.
    # Only clamp syntactically-valid domains that are not in user_domains.
    # Syntactically-invalid domains (emoji, etc.) are left for Pydantic to reject → None.
    llm_domain = data.get("pentagon_domain", "")
    if user_domains and llm_domain not in user_domains and _DOMAIN_RE.match(str(llm_domain)):
        logger.warning(
            f"C60 Topology Engine: LLM returned unknown domain {llm_domain!r} "
            f"— clamping to 'Общее' (valid={user_domains[:3]}…)"
        )
        data["pentagon_domain"] = "Общее"

    # EC-9: validate against C60Atom schema — any deviation → None
    try:
        atom = C60Atom.model_validate(data)
    except ValidationError as e:
        logger.warning(
            f"C60 Topology Engine: schema validation failed — returning None "
            f"(model={model_name}, errors={e.error_count()}, step_label=c60_topology_engine)"
        )
        return None

    logger.info(
        f"C60 Atom created: domain={atom.pentagon_domain!r}, "
        f"bonds={len(atom.covalent_bonds)}, node_id={atom.node_id[:8]}…, "
        f"step_label=c60_topology_engine"
    )
    return atom
