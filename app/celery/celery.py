# app/celery/celery.py
from celery import Celery, signals
from databases import Database # Используется 'databases'
from app.db.database import DATABASE_URL # Убедись, что DATABASE_URL здесь доступен
import asyncio # для run_until_complete
import logging

logger = logging.getLogger(__name__)

# Создаем экземпляр Celery
app = Celery(
    'tasks', # Имя твоего проекта Celery
    broker='redis://localhost:6379/0', # URL твоего брокера Redis
    backend='redis://localhost:6379/0', # URL твоего бэкенда Redis
    include=['app.services.bruteforce'] # Список модулей с задачами Celery
)

# Конфигурация Celery (некоторые параметры)
app.conf.update(
    task_serializer='json',
    accept_content=['json'], # Принимать только JSON
    result_serializer='json',
    timezone='Europe/Moscow', # Укажи свой часовой пояс
    enable_utc=True, # Рекомендуется использовать UTC
    # Ограничение на количество одновременно выполняемых задач на одном воркере, если нужно
    # worker_concurrency=4, # Зависит от CPU и типа задач (I/O bound vs CPU bound)
)

# Глобальный экземпляр базы данных для Celery воркеров
# Этот экземпляр будет подключаться/отключаться с помощью сигналов воркера
# Важно: DATABASE_URL должен быть тем же, что и для FastAPI
celery_db_instance = Database(DATABASE_URL)

@signals.worker_process_init.connect
def init_db_connection(**kwargs):
    """Инициализация соединения с БД при запуске процесса воркера Celery."""
    logger.info("Celery Worker: Инициализация соединения с БД...")
    try:
        # 'databases' требует асинхронного контекста
        loop = asyncio.get_event_loop()
        loop.run_until_complete(celery_db_instance.connect())
        logger.info("Celery Worker: Соединение с БД установлено.")
    except Exception as e:
        logger.error(f"Celery Worker: Ошибка подключения к БД: {e}", exc_info=True)
        # Реши, должен ли воркер падать, если БД недоступна
        raise

@signals.worker_process_shutdown.connect
def close_db_connection(**kwargs):
    """Закрытие соединения с БД при завершении работы процесса воркера Celery."""
    logger.info("Celery Worker: Закрытие соединения с БД...")
    try:
        if celery_db_instance.is_connected:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(celery_db_instance.disconnect())
            logger.info("Celery Worker: Соединение с БД закрыто.")
    except Exception as e:
        logger.error(f"Celery Worker: Ошибка закрытия соединения с БД: {e}", exc_info=True)

# Теперь celery_db_instance можно импортировать и использовать в задачах Celery