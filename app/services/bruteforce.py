# app/services/bruteforce.py
from typing import Optional, Dict, Any
from app.db.database import task_table # Определение таблицы tasks
from app.celery.celery import celery_db_instance # БД для Celery
import itertools
import time
from celery import shared_task
import logging
from app.services.rar_tools import check_rar_password # Твой модуль для RAR
from asgiref.sync import async_to_sync # Для вызова async DB операций из sync Celery
import redis # Синхронный клиент Redis для Celery
import json

logger = logging.getLogger(__name__)

# --- Функция для публикации в Redis из Celery ---
def _publish_notification_to_redis(redis_client: redis.Redis, task_id_str: str, message_content: Dict[str, Any]):
    """Отправляет уведомление в Redis."""
    if not redis_client:
        logger.warning(f"[Task {task_id_str}] Redis клиент недоступен. Пропуск уведомления: {message_content.get('status')}")
        return
    
    channel_name = f"ws:{task_id_str}"
    # Структура сообщения, которую ожидает WebSocketManager в _listen_redis
    payload_to_redis = {
        'task_id': task_id_str,
        'message': message_content
    }
    try:
        json_payload = json.dumps(payload_to_redis)
        redis_client.publish(channel_name, json_payload)
        logger.info(f"[Task {task_id_str}] Опубликовано в Redis канал '{channel_name}': статус {message_content.get('status')}")
    except redis.exceptions.RedisError as e:
        logger.error(f"[Task {task_id_str}] Ошибка Redis при публикации в канал '{channel_name}': {e}")
    except Exception as e:
        logger.error(f"[Task {task_id_str}] Непредвиденная ошибка при публикации в Redis (канал '{channel_name}'): {e}", exc_info=True)


@shared_task(bind=True, name='app.services.bruteforce.bruteforce_rar_task')
def bruteforce_rar_task(self, rar_path: str, charset: str, max_length: int, task_id: int): # task_id из БД (int)
    task_id_str = str(task_id) # Для Redis каналов и сообщений используем строку
    start_time = time.time()
    logger.info(f"[Task {task_id_str}] Запуск bruteforce: rar_path={rar_path}, charset_len={len(charset)}, max_len={max_length}")

    # Инициализация синхронного клиента Redis для Celery задачи
    # ВАЖНО: вынеси параметры подключения (host, port, db) в конфигурацию
    redis_client = None
    try:
        redis_client = redis.Redis(host='localhost', port=6379, db=0)
        redis_client.ping() # Проверка соединения
        logger.info(f"[Task {task_id_str}] Синхронный Redis клиент для Celery подключен.")
    except redis.exceptions.ConnectionError as e:
        logger.error(f"[Task {task_id_str}] Не удалось подключиться к Redis для уведомлений: {e}")
        # Можно решить, должна ли задача падать или продолжаться без уведомлений.
        # Для этой лабораторной уведомления важны.
        redis_client = None 

    # Отправка начального статуса
    start_message = {
        "status": "STARTED", "task_id": task_id_str, "hash_type": "rar",
        "charset_length": len(charset), "max_length": max_length
    }
    _publish_notification_to_redis(redis_client, task_id_str, start_message)

    # Обновление статуса задачи в БД (используем async_to_sync с celery_db_instance)
    async_to_sync(update_task_db_status)(task_id, "running", 0)

    try:
        total_combinations = sum(len(charset)**l for l in range(1, max_length + 1))
        processed_combinations = 0
        last_progress_update_time = time.time()
        password_found = None
        combinations_in_last_second = 0 # Счетчик комбинаций для CPS

        for length in range(1, max_length + 1):
            if password_found: break
            for attempt_tuple in itertools.product(charset, repeat=length):
                if password_found: break

                current_password = "".join(attempt_tuple)
                processed_combinations += 1
                combinations_in_last_second +=1

                current_time = time.time()
                # Обновление прогресса (например, каждую секунду)
                time_since_last_update = current_time - last_progress_update_time
                if time_since_last_update >= 1.0:
                    progress_percentage = int((processed_combinations / total_combinations) * 100) if total_combinations > 0 else 0
                    cps = combinations_in_last_second / time_since_last_update if time_since_last_update > 0 else 0
                    
                    progress_message = {
                        "status": "PROGRESS", "task_id": task_id_str, "progress": progress_percentage,
                        "current_combination": current_password, # Может быть слишком частым, можно убрать или слать реже
                        "combinations_per_second": round(cps, 2)
                    }
                    _publish_notification_to_redis(redis_client, task_id_str, progress_message)
                    
                    # Обновление прогресса в БД
                    async_to_sync(update_task_db_status)(task_id, "running", progress_percentage)
                    
                    combinations_in_last_second = 0 # Сброс счетчика CPS
                    last_progress_update_time = current_time

                # Проверка пароля
                # ВНИМАНИЕ: `check_rar_password` может быть блокирующей операцией.
                # Если она очень долгая, это может замедлить Celery воркер.
                if check_rar_password(rar_path, current_password):
                    password_found = current_password
                    logger.info(f"[Task {task_id_str}] Пароль НАЙДЕН: {password_found}")
                    break 
            if password_found: break
        
        elapsed_time_seconds = time.time() - start_time
        elapsed_time_formatted = time.strftime("%H:%M:%S", time.gmtime(elapsed_time_seconds))

        if password_found:
            async_to_sync(update_task_db_status)(task_id, "completed", 100, result=password_found)
            completed_message = {
                "status": "COMPLETED", "task_id": task_id_str, "result": password_found,
                "elapsed_time": elapsed_time_formatted
            }
            _publish_notification_to_redis(redis_client, task_id_str, completed_message)
            return {"status": "COMPLETED", "result": password_found, "task_id": task_id_str}
        else:
            async_to_sync(update_task_db_status)(task_id, "failed", 100, result="Password not found") # или другой статус
            failed_message = { # Используй "FAILED" или "NOT_FOUND" как в требованиях
                "status": "COMPLETED", # По условию, если не найден, тоже COMPLETED, но без result
                "task_id": task_id_str, 
                "result": None, # Пароль не найден
                "elapsed_time": elapsed_time_formatted
                # "error": "Password not found" # Можно добавить поле error если нужно
            }
            # Если в требованиях для ненайденного пароля другой статус (например, FAILED), измени здесь
            # failed_message["status"] = "FAILED"
            # failed_message["error"] = "Password not found"

            _publish_notification_to_redis(redis_client, task_id_str, failed_message)
            return {"status": failed_message["status"], "result": None, "task_id": task_id_str}

    except Exception as e:
        logger.error(f"[Task {task_id_str}] Ошибка во время bruteforce: {e}", exc_info=True)
        elapsed_time_seconds = time.time() - start_time
        elapsed_time_formatted = time.strftime("%H:%M:%S", time.gmtime(elapsed_time_seconds))
        
        async_to_sync(update_task_db_status)(task_id, "failed", 100, result=str(e)) # Обновляем БД с ошибкой

        error_message_payload = {
            "status": "FAILED", "task_id": task_id_str, "error": str(e),
            "elapsed_time": elapsed_time_formatted
        }
        _publish_notification_to_redis(redis_client, task_id_str, error_message_payload)
        # Celery автоматически пометит задачу как FAILED, если возникло исключение
        raise # Перевыброс исключения, чтобы Celery обработал его как сбой задачи
    finally:
        if redis_client:
            try:
                redis_client.close()
                logger.info(f"[Task {task_id_str}] Синхронный Redis клиент для Celery закрыт.")
            except Exception as e_close:
                 logger.error(f"[Task {task_id_str}] Ошибка при закрытии Redis клиента: {e_close}")


# --- Хелперы для обновления БД из Celery (асинхронные, вызываются через async_to_sync) ---
async def update_task_db_status(task_id: int, status: str, progress: int, result: Optional[str] = None):
    """Обновляет статус, прогресс и результат задачи в БД."""
    values_to_update = {"status": status, "progress": progress}
    if result is not None: # result может быть и пустой строкой, если пароль пустой
        values_to_update["result"] = result
    
    # Убедимся, что celery_db_instance подключен (хотя сигналы должны это гарантировать)
    if not celery_db_instance.is_connected:
        logger.warning(f"[DB Update Task {task_id}] celery_db_instance не подключен! Попытка подключения...")
        try:
            await celery_db_instance.connect()
        except Exception as e_connect:
            logger.error(f"[DB Update Task {task_id}] Ошибка подключения celery_db_instance: {e_connect}")
            return # Не можем обновить БД

    query = task_table.update().where(task_table.c.id == task_id).values(**values_to_update)
    try:
        await celery_db_instance.execute(query)
        logger.info(f"[DB Update Task {task_id}] Статус обновлен: status={status}, progress={progress}, result='{result if result else 'N/A'}'")
    except Exception as e_execute:
        logger.error(f"[DB Update Task {task_id}] Ошибка выполнения запроса к БД: {e_execute}")


# Эта функция используется FastAPI эндпоинтом /get_status, поэтому она должна использовать `database` из FastAPI
from app.db.database import database as fastapi_db_instance # импортируем с псевдонимом
from app.schemas.task import TaskStatus as TaskStatusSchema # схема Pydantic

async def get_task_status(task_id: int) -> Optional[TaskStatusSchema]:
    """Получение статуса задачи из БД для FastAPI эндпоинта."""
    if not fastapi_db_instance.is_connected:
        # Это может быть излишним, если lifespan FastAPI гарантирует подключение для запросов
        logger.warning(f"FastAPI DB (fastapi_db_instance) не подключена при вызове get_task_status для {task_id}, попытка подключения.")
        try:
            await fastapi_db_instance.connect()
        except Exception as e_connect_fastapi:
            logger.error(f"Ошибка подключения fastapi_db_instance в get_task_status: {e_connect_fastapi}")
            return None # Не можем получить статус

    # Используй select() из SQLAlchemy core или select(Task) если у тебя есть ORM модель Task
    # query = task_table.select().where(task_table.c.id == task_id) # Для SQLAlchemy core
    # Для SQLAlchemy ORM (если task_table это __table__ модели Task):
    # query = select(Task).where(Task.id == task_id)
    
    # Пример для SQLAlchemy core:
    query = task_table.select().where(task_table.c.id == task_id)
    task_row = await fastapi_db_instance.fetch_one(query)
    
    if task_row:
        # Преобразуем результат из БД (RowProxy) в Pydantic схему
        return TaskStatusSchema(
            task_id=task_row["id"],
            status=task_row["status"],
            progress=task_row["progress"],
            result=task_row["result"]
        )
    return None