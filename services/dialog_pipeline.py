"""
Оркестратор диалогового пайплайна.
Единый путь обработки сообщения: bulk → vault-quicksave → AI generation → tags → send.
"""

import asyncio
import html
import logging
import os
import re
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from services.ai_engine import parse_model_string
from services.async_utils import fire_and_forget
from services.formatting import markdown_to_telegram_html, split_message
from services.model_data import NVIDIA_MODELS, GEMINI_MODELS, MODEL_HINTS, MODEL_FAMILY_META
from services.model_rotation import generate_with_fallback, format_rotation_footnote
from services.prompt_builder import build_model_context_for_prompt, build_system_prompt, wrap_prompt_with_context
from services.response_tags import parse_response_tags

logger = logging.getLogger(__name__)

# Паттерн быстрого сохранения: "Запиши идею: текст" / "Сохрани промпт: текст"
VAULT_QUICK_SAVE_RE = re.compile(
    r'^(?:запиши|сохрани)\s+(?:идею|промпт|заметку)\s*[:：]\s*(.+)',
    re.IGNORECASE | re.DOTALL,
)

# Ключевые слова для определения intent по моделям
_SWITCH_KEYWORDS = [
    "переключи", "смени модель", "поставь модель", "switch",
]
_RECOMMEND_KEYWORDS = [
    "подбери модель", "модель лучше", "рекомендуй модель",
    "посоветуй модель", "предложи модель",
]
_INFO_KEYWORDS = [
    "какая модель", "какую модель", "какая у тебя модель",
    "на какой модели", "текущая модель",
]


async def resolve_and_switch_model(user_id: int, model_id: str, db_service, model_catalog, ai_engine) -> str | None:
    """Резолвит model_id и сохраняет в Firestore. Возвращает resolved model string или None."""
    if model_id in GEMINI_MODELS:
        selected = f"gemini/{GEMINI_MODELS[model_id]}"
        await db_service.update_user(user_id, {"selected_model": selected})
        return model_id
    if model_id in NVIDIA_MODELS:
        selected = f"nvidia/{NVIDIA_MODELS[model_id]}"
        await db_service.update_user(user_id, {"selected_model": selected})
        return model_id

    provider, model = parse_model_string(model_id)
    if provider and model:
        if model_catalog and model_catalog.is_available:
            models = await model_catalog.get_models()
            if model_id in models:
                await db_service.update_user(user_id, {"selected_model": f"litellm/{model_id}"})
                return model_id
        try:
            ai_engine.get_provider(provider, model)
            await db_service.update_user(user_id, {"selected_model": f"{provider}/{model}"})
            return model_id
        except ValueError:
            pass

    return None


class DialogPipeline:
    """
    Оркестратор обработки сообщения пользователя.
    Получает зависимости через конструктор, не захватывает closure-состояние.
    """

    def __init__(self, db_service, ai_engine, memory_service=None, analyzer_service=None, model_catalog=None, graph=None):
        self.db = db_service
        self.ai = ai_engine
        self.memory = memory_service
        self.analyzer = analyzer_service
        self.catalog = model_catalog
        self.graph = graph  # Compiled LangGraph (None = fallback to old path)

    async def process_turn(self, user, chat_id, user_text, context, state):
        """
        Главный метод — обрабатывает один диалоговый ход.

        Args:
            user: telegram.User
            chat_id: int
            user_text: str — текст (или транскрипция голоса)
            context: telegram ContextTypes.DEFAULT_TYPE
            state: BotState — для bulk-режима
        """
        turn_start = time.time()
        run_id = uuid.uuid4().hex[:8]
        try:
            # 0. Bulk-режим
            if state.is_bulk(user.id):
                await self._handle_bulk(user.id, chat_id, user_text, context, state)
                return

            # 0.5. Быстрое сохранение в vault
            vault_match = VAULT_QUICK_SAVE_RE.match(user_text.strip())
            if vault_match:
                await self._handle_vault_quicksave(user.id, chat_id, user_text, vault_match, context)
                return

            # 1. Получаем/создаём пользователя
            user_data = {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": user.is_bot,
            }
            db_user = await self.db.get_or_create_user(user.id, user_data)

            # 2. Сохраняем сообщение пользователя
            try:
                await self.db.save_message(user.id, "user", user_text)
            except Exception as save_err:
                logger.warning(f"Не удалось сохранить сообщение пользователя {user.id}: {save_err}")

            # 3. Typing indicator
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # 4. История
            history = await self.db.get_last_messages(user.id, limit=20)
            if history and history[-1]['content'] == user_text and history[-1]['role'] == 'user':
                history_for_ai = history[:-1]
            else:
                history_for_ai = history

            # 5. Определяем провайдер пользователя
            user_provider = None
            user_model = None
            user_model_str = db_user.get('selected_model') if db_user else None
            if user_model_str:
                user_provider, user_model = parse_model_string(user_model_str)

            # 5.1. Долговременная память
            memory_context = ""
            if self.memory:
                try:
                    memory_context = await self.memory.get_memory_context(user.id, user_text)
                except Exception as mem_err:
                    logger.warning(f"Memory search error: {mem_err}")

            # 6. Генерируем ответ
            model_context, is_recommend_intent = self._detect_model_intent(user_text)
            req_provider = user_provider or self.ai.default_provider_name
            req_model = user_model or self.ai.default_model

            if self.graph:
                # === LangGraph path: router → generate → analyzer ===
                graph_input = {
                    "user_id": user.id,
                    "user_name": user.first_name or "",
                    "user_message": user_text,
                    "messages": list(history_for_ai),
                    "user_profile": db_user or {},
                    "memory_context": memory_context,
                    "model_context": model_context or "",
                    "selected_provider": req_provider,
                    "selected_model": req_model,
                }
                result = await self._run_with_typing(
                    self.graph.ainvoke(graph_input), context, chat_id
                )
                response_text = result.get("response", "")
                actual_provider = result.get("actual_provider", req_provider)
                actual_model = result.get("actual_model", req_model)
                graph_intent = result.get("intent", "")
            else:
                # === Legacy path (без LangGraph) ===
                current_model_str = f"{req_provider}/{req_model}"
                system_prompt = build_system_prompt(db_user, user.first_name, model_context=model_context, current_model=current_model_str)
                system_prompt = wrap_prompt_with_context(system_prompt, memory_context)
                messages_for_ai = list(history_for_ai) + [{"role": "user", "content": user_text}]

                response_text, actual_provider, actual_model = await generate_with_fallback(
                    ai_engine=self.ai,
                    model_catalog=self.catalog,
                    provider_name=req_provider,
                    model=req_model,
                    messages=messages_for_ai,
                    system_prompt=system_prompt,
                    timeout=30.0,
                )
                graph_intent = ""

            # 6a. Парсим теги
            tag_result = parse_response_tags(response_text, self.catalog)
            response_text = tag_result.clean_text

            # 6b. Выполняем действия из тегов
            response_text = await self._execute_tag_actions(user.id, response_text, tag_result.actions)

            # Footnote при ротации
            rotation_footnote = format_rotation_footnote(
                req_provider, req_model, actual_provider, actual_model
            )

            # 6c. Сохраняем ответ
            try:
                await self.db.save_message(user.id, "assistant", response_text)
            except Exception as save_err:
                logger.warning(f"Не удалось сохранить ответ ассистента для {user.id}: {save_err}")

            # 6.1 Долговременная память (фоново)
            if self.memory:
                await self.memory.store_conversation(user.id, user_text, response_text)

            # 6.2 Анализ профиля
            if self.analyzer:
                if self.graph:
                    # LangGraph: LLM-классификация async после отправки ответа (не блокирует)
                    fire_and_forget(
                        self._async_analyzer_check(user.id, user_text),
                        name=f"analyzer-check-{user.id}",
                    )
                else:
                    # Legacy: каждые 3 сообщения
                    try:
                        user_messages_count = len([m for m in history if m.get('role') == 'user'])
                        if user_messages_count > 0 and user_messages_count % 3 == 0:
                            logger.info(f"Запускаем анализ профиля для {user.id} (после {user_messages_count} сообщений)")
                            fire_and_forget(self.analyzer.analyze_user_profile(user.id), name=f"analyze-{user.id}")
                    except Exception as analyzer_error:
                        logger.warning(f"Не удалось запустить анализ профиля: {analyzer_error}")

            # 7. Форматируем и отправляем
            display_text = response_text + rotation_footnote if rotation_footnote else response_text
            formatted_response = markdown_to_telegram_html(display_text)

            recommend_keyboard = None
            if is_recommend_intent and not tag_result.actions:
                recommend_keyboard = self._build_recommendation_keyboard()

            message_parts = split_message(formatted_response)

            for i, part in enumerate(message_parts):
                reply_markup = recommend_keyboard if (i == len(message_parts) - 1 and recommend_keyboard) else None
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                except Exception:
                    logger.warning(f"Ошибка отправки с HTML для {user.id}, отправляем без форматирования")
                    await context.bot.send_message(chat_id=chat_id, text=response_text[:4096])

            # Pipeline-level log (structured for Cloud Run queryability)
            latency_ms = int((time.time() - turn_start) * 1000)
            tag_types = [a.type for a in tag_result.actions] if tag_result.actions else []
            logger.info(
                "Pipeline complete",
                extra={
                    "run_id": run_id,
                    "user_id": user.id,
                    "intent": graph_intent or "legacy",
                    "req_model": f"{req_provider}/{req_model}",
                    "actual_model": f"{actual_provider}/{actual_model}",
                    "memory": bool(memory_context),
                    "tags": tag_types,
                    "latency_ms": latency_ms,
                    "graph": bool(self.graph),
                },
            )

        except Exception as e:
            logger.error(f"Ошибка при обработке диалога с {user.id}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка при обработке вашего сообщения.")

    # --- Вспомогательные методы ---

    @staticmethod
    async def _run_with_typing(coro, context, chat_id, interval: float = 4.0):
        """Запускает coroutine и обновляет typing indicator каждые `interval` секунд."""
        async def _typing_loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    pass  # Typing — best-effort, не блокируем при ошибке

        typing_task = asyncio.create_task(_typing_loop())
        try:
            return await coro
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def _async_analyzer_check(self, user_id: int, user_text: str):
        """Post-graph async: LLM решает нужен ли анализ профиля, если да — запускает."""
        from services.graph.nodes import ANALYZER_PROMPT, ANALYZER_TIMEOUT, parse_analyzer_response
        try:
            provider = self.ai.get_provider(self.ai.default_provider_name, self.ai.default_model)
            raw = await asyncio.wait_for(
                provider.generate(
                    messages=[{"role": "user", "content": ANALYZER_PROMPT.format(user_message=user_text)}],
                    system_prompt="",
                    temperature=0,
                ),
                timeout=ANALYZER_TIMEOUT,
            )
            if parse_analyzer_response(raw):
                logger.info("Analyzer: new info detected for user %d, triggering analysis", user_id)
                await self.analyzer.analyze_user_profile(user_id)
        except Exception as exc:
            logger.warning("Analyzer check failed for user %d: %s", user_id, type(exc).__name__)

    async def _handle_bulk(self, user_id, chat_id, user_text, context, state):
        """Обработка сообщения в bulk-режиме."""
        if self.memory:
            await self.memory.store_bulk(user_id, user_text)
            count = state.increment_bulk(user_id)
            if count % 5 == 0:
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Загружено: {count}")
            else:
                await context.bot.send_message(chat_id=chat_id, text="✅")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ Memory Service не доступен.")

    async def _handle_vault_quicksave(self, user_id, chat_id, user_text, vault_match, context):
        """Быстрое сохранение в vault."""
        content = vault_match.group(1).strip()
        if not content:
            return

        lower = user_text.lower()
        if "промпт" in lower:
            item_type = "prompt"
        elif "идею" in lower or "идея" in lower:
            item_type = "idea"
        else:
            item_type = "note"

        title = content[:60] + ("..." if len(content) > 60 else "")
        try:
            await self.db.vault_save(user_id, title, content, item_type=item_type)
            type_label = {"prompt": "Промпт", "idea": "Идея", "note": "Заметка"}
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ {type_label[item_type]} сохранена в хранилище: <b>{html.escape(title)}</b>",
                parse_mode=ParseMode.HTML,
            )
        except ValueError as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {e}")
        except Exception as e:
            logger.error(f"Vault quick-save error for {user_id}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось сохранить.")

    def _detect_model_intent(self, user_text: str) -> tuple[str | None, bool]:
        """Определяет intent пользователя относительно моделей. Возвращает (model_context, is_recommend)."""
        model_context = None
        is_recommend_intent = False
        user_text_lower = user_text.lower()

        if any(kw in user_text_lower for kw in _SWITCH_KEYWORDS + _RECOMMEND_KEYWORDS + _INFO_KEYWORDS):
            model_context = build_model_context_for_prompt(GEMINI_MODELS, NVIDIA_MODELS, MODEL_FAMILY_META)
        if any(kw in user_text_lower for kw in _RECOMMEND_KEYWORDS):
            is_recommend_intent = True

        return model_context, is_recommend_intent

    async def _execute_tag_actions(self, user_id: int, response_text: str, actions: list) -> str:
        """Выполняет действия из распарсенных тегов. Возвращает обновлённый текст."""
        for action in actions:
            if action.type == "switch_model":
                try:
                    resolved = await self._resolve_and_switch_model(user_id, action.model_id)
                    if resolved:
                        display_name = MODEL_HINTS.get(resolved, resolved)
                        response_text += f"\n\n✅ Модель переключена: {display_name}"
                    else:
                        response_text += f"\n\n⚠️ Модель {action.model_id} сейчас недоступна, оставляю текущую."
                except Exception as switch_err:
                    logger.error(f"Ошибка переключения модели для {user_id}: {switch_err}")
                    response_text += "\n\n⚠️ Не удалось переключить модель."

            elif action.type == "vault_save":
                try:
                    await self.db.vault_save(
                        user_id,
                        action.vault_title,
                        action.vault_content,
                        item_type=action.vault_type,
                    )
                    type_label = {"prompt": "Промпт", "idea": "Идея", "note": "Заметка"}
                    response_text += f"\n\n✅ {type_label.get(action.vault_type, 'Заметка')} сохранена в /vault"
                except Exception as vault_err:
                    logger.error(f"Vault save via tag error for {user_id}: {vault_err}")
                    response_text += "\n\n⚠️ Не удалось сохранить в хранилище."

        return response_text

    async def _resolve_and_switch_model(self, user_id: int, model_id: str) -> str | None:
        """Делегирует в standalone-функцию."""
        return await resolve_and_switch_model(user_id, model_id, self.db, self.catalog, self.ai)

    @staticmethod
    def _build_recommendation_keyboard() -> InlineKeyboardMarkup:
        """Inline keyboard с популярными моделями — только с доступными API-ключами."""
        _popular = [
            ("✨ Gemini 2.5 Flash — быстрая", "mswitch:gemini-2.5-flash", "GEMINI_API_KEY"),
            ("✨ Gemini 2.5 Pro — мощная", "mswitch:gemini-2.5-pro", "GEMINI_API_KEY"),
            ("🟣 Claude Sonnet 4 — умная", "mswitch:claude", "ANTHROPIC_API_KEY"),
            ("⚪ GPT-4o — универсальная", "mswitch:gpt", "OPENAI_API_KEY"),
            ("🔵 Kimi K2 — рассуждения", "mswitch:kimi-k2", "NVIDIA_API_KEY"),
            ("🟠 DeepSeek V3 — код и анализ", "mswitch:deepseek-v3.2", "NVIDIA_API_KEY"),
        ]
        buttons = []
        for label, callback, env_key in _popular:
            if os.getenv(env_key):
                buttons.append([InlineKeyboardButton(label, callback_data=callback)])

        buttons.append([InlineKeyboardButton("📋 Все модели →", callback_data="mc:categories")])
        buttons.append([InlineKeyboardButton("✅ Оставить текущую", callback_data="mswitch:keep")])
        return InlineKeyboardMarkup(buttons)
