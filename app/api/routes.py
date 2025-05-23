# app/api/routes.py
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from app.schemas.task import TaskStatus # Твоя Pydantic схема
from app.db.database import database, task_table # Для создания задачи
from app.celery.celery import app as celery_app_instance # Экземпляр Celery из celery.py
import tempfile
import logging
from app.services.bruteforce import get_task_status # Функция для GET /status
from app.websocket.manager import ws_manager # Твой WebSocketManager

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/brut_hash", response_model=TaskStatus) # Используем TaskStatus для ответа
async def brut_hash(
    charset: str = Form(default="abcdefghijklmnopqrstuvwxyz0123456789"),
    max_length: int = Form(default=5, le=8), # le=8 - максимальная длина 8
    rar_file: UploadFile = File(...)
):
    logger.info(f"POST /brut_hash: charset='{charset}', max_length={max_length}, file='{rar_file.filename}'")
    try:
        # Сохраняем загруженный файл во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".rar") as tmp_rar_file:
            content = await rar_file.read()
            tmp_rar_file.write(content)
            tmp_rar_file_path = tmp_rar_file.name
        logger.info(f"Файл сохранен во временный путь: {tmp_rar_file_path}")

        # Создаем запись о задаче в БД
        # Статус "pending" или "queued", т.к. задача еще не начала выполняться воркером
        insert_query = task_table.insert().values(
            file_path=tmp_rar_file_path, # Сохраняем путь к файлу
            charset=charset,
            max_length=max_length,
            status="pending", # Начальный статус
            progress=0,
            result=None
        )
        if not database.is_connected: # Убедимся, что БД подключена
            await database.connect()
        
        task_id = await database.execute(insert_query) # Получаем ID созданной задачи
        logger.info(f"Задача создана в БД с ID: {task_id}")

        # Отправляем задачу в Celery
        # Передаем task_id (int), чтобы воркер знал, какую запись в БД обновлять
        celery_app_instance.send_task(
            'app.services.bruteforce.bruteforce_rar_task', # Имя задачи
            args=[tmp_rar_file_path, charset, max_length, task_id] # Аргументы для задачи
        )
        logger.info(f"Задача {task_id} отправлена в Celery")

        # Возвращаем клиенту информацию о созданной задаче
        return TaskStatus(
            task_id=task_id,
            status="pending", # Или "queued"
            progress=0,
            result=None
        )
    except Exception as e:
        logger.error(f"Ошибка в эндпоинте /brut_hash: {e}", exc_info=True)
        # Здесь можно удалить временный файл, если он был создан, но это опционально
        # import os
        # if 'tmp_rar_file_path' in locals() and os.path.exists(tmp_rar_file_path):
        # os.unlink(tmp_rar_file_path)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")


@router.get("/get_status/{task_id}", response_model=Optional[TaskStatus]) # Может вернуть TaskStatus или null (404)
async def get_status_route(task_id: int): # task_id здесь int
    logger.info(f"GET /get_status/{task_id}")
    task_data = await get_task_status(task_id) # Эта функция должна вернуть Pydantic модель или None
    if task_data is None:
        logger.warning(f"Задача с ID {task_id} не найдена для GET /get_status")
        raise HTTPException(
            status_code=404,
            detail=f"Task with id {task_id} not found"
        )
    return task_data


@router.websocket("/ws/{task_id_str}") # task_id здесь будет строкой из URL
async def websocket_endpoint(websocket: WebSocket, task_id_str: str):
    # task_id_str - это ID задачи, полученный от клиента (строка)
    # Убедимся, что такая задача существует (опционально, но полезно)
    # try:
    #     task_id_int = int(task_id_str)
    #     task_exists = await get_task_status(task_id_int) # Проверка существования
    #     if not task_exists:
    #         logger.warning(f"WebSocket: Попытка подключения к несуществующей задаче {task_id_str}")
    #         await websocket.close(code=1008) # Policy Violation
    #         return
    # except ValueError:
    #     logger.warning(f"WebSocket: Некорректный task_id в URL: {task_id_str}")
    #     await websocket.close(code=1007) # Invalid frame payload data
    #     return

    await ws_manager.connect(task_id_str, websocket)
    logger.info(f"WebSocket /ws/{task_id_str}: клиент подключен.")
    try:
        while True:
            # Здесь можно обрабатывать команды от клиента, если они нужны (pause/resume/cancel)
            # data = await websocket.receive_text()
            # logger.info(f"WebSocket /ws/{task_id_str}: получено сообщение от клиента: {data}")
            # if data == "pause": ...
            await websocket.receive_text() # Просто держим соединение открытым для получения сообщений от сервера
            # Для данной задачи клиент в основном слушает, а не шлет команды после подключения.
            # Если команды не нужны, можно заменить на asyncio.sleep в цикле или просто ждать disconnect.
            # Если ты не ожидаешь сообщений от клиента, то можно убрать receive_text() и положиться на keepalive пинги
            # или на то, что соединение будет разорвано клиентом или по таймауту.
            # Однако, receive_text() также обрабатывает закрытие соединения клиентом.
            # Если не нужен прием команд, но нужно отслеживать разрыв:
            # try:
            #     while True: await asyncio.sleep(3600) # Ждем очень долго
            # except asyncio.CancelledError: raise # Позволяем FastAPI отменить задачу
            # finally: await ws_manager.disconnect(task_id_str, websocket)

    except WebSocketDisconnect:
        logger.info(f"WebSocket /ws/{task_id_str}: клиент отключился.")
    except Exception as e:
        logger.error(f"WebSocket /ws/{task_id_str}: ошибка: {e}", exc_info=True)
    finally:
        # Убедимся, что соединение корректно закрывается и удаляется из менеджера
        await ws_manager.disconnect(task_id_str, websocket)
        logger.info(f"WebSocket /ws/{task_id_str}: ресурсы соединения освобождены.")