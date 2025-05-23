from fastapi import FastAPI
from app.api.routes import router
from app.db.database import database, engine, metadata
from contextlib import asynccontextmanager
import logging
import uvicorn
import redis
from app.websocket.manager import ws_manager
import asyncio

# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация Redis
    logger.info("Initializing Redis...")
    app.state.redis = redis.Redis(host='localhost', port=6379, db=0)
    
    # Подключение к основной БД
    logger.info("Connecting to the database...")
    await database.connect()
    metadata.create_all(engine)
    logger.info("Database connected.")
    
    # Запускаем слушатель Redis для WebSocket
    asyncio.create_task(ws_manager.listen_redis())
    
    yield
    
    # Завершение работы
    logger.info("Disconnecting from the database...")
    await database.disconnect()
    logger.info("Database disconnected.")
    
    logger.info("Closing Redis...")
    app.state.redis.close()
    logger.info("Redis closed.")

# Создаем экземпляр приложения ПОСЛЕ определения lifespan
app = FastAPI(lifespan=lifespan)
app.include_router(router)

@app.get("/")
async def read_root():
    logger.info("Root endpoint was called")
    return {"message": "Hello World"}

# main.py
from fastapi import FastAPI
from app.api.routes import router
from app.websocket.manager import ws_manager
import asyncio

app = FastAPI()

app.include_router(router)

@app.on_event("startup")
async def startup():
    await ws_manager.initialize()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)