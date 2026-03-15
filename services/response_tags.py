"""
Пост-процессор тегов в ответах LLM.
Парсит [SWITCH_MODEL: model_id] из ответа AI, валидирует,
возвращает чистый текст + действия. Теги НИКОГДА не показываются пользователю.
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Regex для тега переключения модели (Willison: handle whitespace variations)
_SWITCH_MODEL_RE = re.compile(r'\[SWITCH_MODEL:\s*([a-zA-Z0-9._/\-]+)\s*\]')

# Regex для тега сохранения в vault: [VAULT_SAVE: type | title | content]
_VAULT_SAVE_RE = re.compile(r'\[VAULT_SAVE:\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\]', re.DOTALL)


@dataclass
class TagAction:
    """Одно действие, извлечённое из тега."""
    type: str           # "switch_model", "vault_save"
    model_id: str = ""  # Для switch_model — ID модели
    vault_type: str = ""    # Для vault_save: prompt/idea/note
    vault_title: str = ""   # Для vault_save: заголовок
    vault_content: str = "" # Для vault_save: содержимое


@dataclass
class PostProcessResult:
    """Результат парсинга тегов."""
    clean_text: str             # Текст без тегов — для пользователя и хранения в истории
    actions: list = field(default_factory=list)   # Список TagAction
    errors: list = field(default_factory=list)    # Ошибки валидации


def parse_response_tags(response_text: str, model_catalog=None) -> PostProcessResult:
    """
    Парсит теги из ответа LLM. Чистая функция — не выполняет действия.

    - Извлекает все [SWITCH_MODEL: ...] теги
    - Валидирует model_id против каталога (если доступен)
    - Вырезает ВСЕ теги из текста (даже невалидные — теги не должны попадать к пользователю)
    - Возвращает PostProcessResult с clean_text и действиями

    Args:
        response_text: Сырой ответ LLM (может содержать теги)
        model_catalog: ModelCatalog для валидации (None = пропустить валидацию)

    Returns:
        PostProcessResult
    """
    actions = []
    errors = []

    # Ищем все теги SWITCH_MODEL
    for match in _SWITCH_MODEL_RE.finditer(response_text):
        model_id = match.group(1).strip()

        if not model_id:
            errors.append("Пустой model_id в теге SWITCH_MODEL")
            continue

        actions.append(TagAction(type="switch_model", model_id=model_id))

    # Ищем все теги VAULT_SAVE
    for match in _VAULT_SAVE_RE.finditer(response_text):
        vault_type = match.group(1).strip().lower()
        vault_title = match.group(2).strip()
        vault_content = match.group(3).strip()

        if vault_type not in ("prompt", "idea", "note"):
            vault_type = "note"

        if vault_title and vault_content:
            actions.append(TagAction(
                type="vault_save",
                vault_type=vault_type,
                vault_title=vault_title[:200],
                vault_content=vault_content[:10000],
            ))

    # Вырезаем ВСЕ теги из текста — независимо от валидации
    clean_text = _SWITCH_MODEL_RE.sub('', response_text)
    clean_text = _VAULT_SAVE_RE.sub('', clean_text).strip()

    # Убираем лишние пустые строки, оставшиеся после удаления тегов
    while '\n\n\n' in clean_text:
        clean_text = clean_text.replace('\n\n\n', '\n\n')

    return PostProcessResult(
        clean_text=clean_text,
        actions=actions,
        errors=errors,
    )
