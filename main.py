# main.py
from fastapi import FastAPI
from app.api.routes import router
from app.db.database import database, engine, metadata # Убедись, что это твоя основная БД
from contextlib import asynccontextmanager
import logging
from app.websocket.manager import ws_manager # твой WebSocketManager
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifespan: Запуск приложения...")
    
    # Инициализация WebSocketManager (включая Redis и слушателя)
    logger.info("Lifespan: Инициализация WebSocketManager...")
    try:
        await ws_manager.initialize() # Этот метод должен настроить Redis и запустить _listen_redis
        logger.info("Lifespan: WebSocketManager успешно инициализирован.")
    except Exception as e:
        logger.error(f"Lifespan: Ошибка инициализации WebSocketManager: {e}", exc_info=True)
        # Можно решить, стоит ли продолжать работу приложения, если WS не работает
        # raise # Если критично

    # Подключение к основной БД
    logger.info("Lifespan: Подключение к базе данных...")
    try:
        await database.connect()
        # metadata.create_all(engine) # Обычно для разработки, чтобы создать таблицы. Будь осторожен в продакшене.
        logger.info("Lifespan: База данных успешно подключена.")
    except Exception as e:
        logger.error(f"Lifespan: Ошибка подключения к базе данных: {e}", exc_info=True)
        # raise # Если критично

    yield

    # Завершение работы
    logger.info("Lifespan: Завершение работы приложения...")
    
    logger.info("Lifespan: Закрытие ресурсов WebSocketManager...")
    if ws_manager.listener_task and not ws_manager.listener_task.done():
        ws_manager.listener_task.cancel()
        try:
            await ws_manager.listener_task
        except asyncio.CancelledError:
            logger.info("Lifespan: Задача слушателя Redis в WebSocketManager отменена.")
        except Exception as e_task_cancel:
            logger.error(f"Lifespan: Ошибка при отмене задачи слушателя Redis: {e_task_cancel}")
            
    if ws_manager.pubsub:
        try:
            await ws_manager.pubsub.close()
            logger.info("Lifespan: PubSub WebSocketManager закрыт.")
        except Exception as e_ps_close:
            logger.error(f"Lifespan: Ошибка закрытия PubSub WebSocketManager: {e_ps_close}")

    if ws_manager.redis_client: # Используем redis_client как имя атрибута в WebSocketManager
        try:
            await ws_manager.redis_client.close()
            logger.info("Lifespan: Соединение WebSocketManager с Redis закрыто.")
        except Exception as e_r_close:
            logger.error(f"Lifespan: Ошибка закрытия соединения WebSocketManager с Redis: {e_r_close}")
    
    if database.is_connected:
        logger.info("Lifespan: Отключение от базы данных...")
        await database.disconnect()
        logger.info("Lifespan: База данных отключена.")

app = FastAPI(lifespan=lifespan)
app.include_router(router)

@app.get("/")
async def read_root():
    logger.info("Root endpoint был вызван")
    return {"message": "Hello World from FastAPI"}

# if __name__ == "__main__":
# import uvicorn
# uvicorn.run(app, host="0.0.0.0", port=8000) # Ты запускаешь через `uvicorn main:app --reload`