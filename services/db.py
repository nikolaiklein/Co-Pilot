import logging
import time
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPICallError

# Настройка логирования для сервиса базы данных
logger = logging.getLogger(__name__)

class DatabaseService:
    """
    Сервис для работы с базой данных Firestore.
    Все методы асинхронны.
    """

    def __init__(self):
        """
        Инициализация клиента Firestore.
        Предполагается, что переменная окружения GOOGLE_APPLICATION_CREDENTIALS задана
        или используются Default Credentials (как в Cloud Run).
        """
        self.db = None

    async def initialize(self):
        """
        Асинхронная инициализация клиента.
        Создает экземпляр AsyncClient.
        """
        try:
            # Используем асинхронный клиент Firestore
            self.db = firestore.AsyncClient()
            logger.info("Клиент Firestore успешно инициализирован.")
        except Exception as e:
            logger.error(f"Ошибка при инициализации Firestore клиента: {e}")
            raise e

    async def get_or_create_user(self, user_id: int, user_data: dict) -> dict:
        """
        Получает пользователя по ID или создает нового, если он не существует.

        Args:
            user_id (int): Telegram User ID.
            user_data (dict): Данные пользователя (username, first_name и т.д.).

        Returns:
            dict: Данные пользователя.
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            # Ссылка на документ пользователя в коллекции 'users'
            user_ref = self.db.collection('users').document(str(user_id))
            user_doc = await user_ref.get()

            if user_doc.exists:
                logger.info(f"Пользователь {user_id} найден в базе.")
                return user_doc.to_dict()
            else:
                logger.info(f"Создание нового пользователя {user_id}.")
                # Добавляем поле created_at
                user_data['created_at'] = firestore.SERVER_TIMESTAMP
                await user_ref.set(user_data)
                return user_data
        except GoogleAPICallError as e:
            logger.error(f"Ошибка Firestore при работе с пользователем {user_id}: {e}")
            raise e
        except Exception as e:
            logger.error(f"Неизвестная ошибка в get_or_create_user: {e}")
            raise e

    async def get_user(self, user_id: int) -> dict | None:
        """
        Получает данные пользователя по ID.

        Args:
            user_id (int): Telegram User ID.

        Returns:
            dict | None: Данные пользователя или None, если не найден.
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            user_ref = self.db.collection('users').document(str(user_id))
            user_doc = await user_ref.get()
            
            if user_doc.exists:
                return user_doc.to_dict()
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении пользователя {user_id}: {e}")
            return None

    async def save_message(self, user_id: int, role: str, content: str):
        """
        Сохраняет сообщение в подколлекцию 'messages' документа пользователя.

        Args:
            user_id (int): Telegram User ID.
            role (str): Роль отправителя ('user' или 'assistant').
            content (str): Текст сообщения.
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            # Ссылка на подколлекцию messages внутри документа пользователя
            messages_ref = self.db.collection('users').document(str(user_id)).collection('messages')

            message_data = {
                'role': role,
                'content': content,
                'timestamp': firestore.SERVER_TIMESTAMP
            }

            await messages_ref.add(message_data)
            logger.info(f"Сообщение для пользователя {user_id} сохранено (role: {role}).")
        except Exception as e:
            logger.error(f"Ошибка при сохранении сообщения для {user_id}: {e}")
            # Не выбрасываем исключение, чтобы не прерывать работу бота, но логируем ошибку
            pass

    async def get_last_messages(self, user_id: int, limit: int = 10) -> list:
        """
        Получает последние сообщения пользователя для формирования контекста.

        Args:
            user_id (int): Telegram User ID.
            limit (int): Количество последних сообщений.

        Returns:
            list: Список словарей с сообщениями, отсортированный по времени (от старых к новым).
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            messages_ref = self.db.collection('users').document(str(user_id)).collection('messages')

            # Получаем последние сообщения, сортируем по убыванию времени, берем лимит
            query = messages_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
            docs = await query.get()

            messages = []
            for doc in docs:
                data = doc.to_dict()
                # Удаляем timestamp из выдачи, если он не сериализуем, или оставляем для логики
                # Для LLM обычно нужны только role и content.
                messages.append(data)

            # Разворачиваем список, чтобы сообщения шли в хронологическом порядке
            return messages[::-1]

        except Exception as e:
            logger.error(f"Ошибка при получении истории сообщений для {user_id}: {e}")
            return []

    async def update_user(self, user_id: int, data: dict):
        """
        Обновляет поля документа пользователя.

        Args:
            user_id (int): Telegram User ID.
            data (dict): Словарь с обновляемыми полями.
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            user_ref = self.db.collection('users').document(str(user_id))
            # update обновляет только указанные поля, не перезаписывая весь документ
            await user_ref.update(data)
            logger.info(f"Пользователь {user_id} обновлен.")
        except Exception as e:
            logger.error(f"Ошибка при обновлении пользователя {user_id}: {e}")
            # Возможно, стоит кидать исключение, если обновление критично
            pass

    async def save_report(self, user_id: int, report_data: dict):
        """
        Сохраняет отчет анализа в подколлекцию 'reports' документа пользователя.

        Args:
            user_id (int): Telegram User ID.
            report_data (dict): Данные отчета (анализ, дата и т.д.).
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            reports_ref = self.db.collection('users').document(str(user_id)).collection('reports')
            # Добавляем timestamp сервера
            report_data['timestamp'] = firestore.SERVER_TIMESTAMP
            await reports_ref.add(report_data)
            logger.info(f"Отчет для пользователя {user_id} сохранен.")
        except Exception as e:
            logger.error(f"Ошибка при сохранении отчета для {user_id}: {e}")
            pass

    async def clear_messages(self, user_id: int) -> int:
        """
        Очищает все сообщения пользователя.

        Args:
            user_id (int): Telegram User ID.

        Returns:
            int: Количество удаленных сообщений.
        """
        if not self.db:
             raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            messages_ref = self.db.collection('users').document(str(user_id)).collection('messages')
            docs = messages_ref.stream()
            
            count = 0
            async for doc in docs:
                await doc.reference.delete()
                count += 1
            
            logger.info(f"Удалено {count} сообщений для пользователя {user_id}.")
            return count
        except Exception as e:
            logger.error(f"Ошибка при очистке сообщений для {user_id}: {e}")
            return 0

    async def get_allowed_users(self) -> set[int] | None:
        """
        Загружает список разрешённых пользователей из Firestore.

        Returns:
            set[int] | None: Множество user_id или None, если документ не найден.
        """
        if not self.db:
            raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            doc_ref = self.db.collection('settings').document('allowed_users')
            doc = await doc_ref.get()

            if doc.exists:
                data = doc.to_dict()
                uids = data.get('user_ids', [])
                result = {int(uid) for uid in uids}
                logger.info(f"Загружено {len(result)} разрешённых пользователей из Firestore.")
                return result
            return None
        except Exception as e:
            logger.error(f"Ошибка при загрузке allowed_users из Firestore: {e}")
            return None

    async def save_allowed_users(self, user_ids: set[int]) -> bool:
        """
        Сохраняет список разрешённых пользователей в Firestore.

        Args:
            user_ids: Множество разрешённых Telegram user_id.

        Returns:
            bool: True если успешно сохранено.
        """
        if not self.db:
            raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            doc_ref = self.db.collection('settings').document('allowed_users')
            await doc_ref.set({
                'user_ids': sorted(list(user_ids)),
                'updated_at': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Сохранено {len(user_ids)} разрешённых пользователей в Firestore.")
            return True
        except Exception as e:
            logger.error(f"Ошибка при сохранении allowed_users в Firestore: {e}")
            return False

    async def get_all_user_ids(self) -> list:
        """
        Получает список всех ID пользователей из БД.
        Используется для batch-обработки (cron jobs).

        Returns:
            list: Список user_id всех пользователей.
        """
        if not self.db:
            raise RuntimeError("DatabaseService не инициализирован. Вызовите initialize().")

        try:
            users_ref = self.db.collection('users')
            docs = users_ref.stream()
            
            user_ids = []
            async for doc in docs:
                # ID документа = user_id
                user_ids.append(int(doc.id))
            
            logger.info(f"Найдено {len(user_ids)} пользователей для обработки.")
            return user_ids
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей: {e}")
            return []
