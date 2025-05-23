# app/websocket/manager.py
from typing import Dict, Set, Any
from fastapi import WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis # Используем alias для ясности
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.redis_client: aioredis.Redis | None = None
        self.pubsub: aioredis.client.PubSub | None = None
        self.listener_task: asyncio.Task | None = None
        # Адрес Redis, лучше вынести в конфигурацию
        self.redis_url = "redis://localhost:6379/0"


    async def initialize(self):
        if not self.redis_client:
            logger.info("WebSocketManager: Инициализация Redis клиента и PubSub...")
            try:
                # decode_responses=False, т.к. pubsub ожидает bytes для channel и data
                self.redis_client = aioredis.from_url(self.redis_url, decode_responses=False)
                await self.redis_client.ping()
                logger.info("WebSocketManager: Redis клиент успешно подключен.")
                self.pubsub = self.redis_client.pubsub()
                # Запускаем задачу прослушивания Redis в фоне
                self.listener_task = asyncio.create_task(self._listen_redis())
                logger.info("WebSocketManager: Задача прослушивания Redis создана и запущена.")
            except Exception as e:
                logger.error(f"WebSocketManager: Ошибка инициализации Redis: {e}", exc_info=True)
                self.redis_client = None
                self.pubsub = None
                raise

    async def _get_channel_name(self, task_id: str) -> str:
        return f"ws:{task_id}"

    async def connect(self, task_id: str, websocket: WebSocket):
        if not self.redis_client or not self.pubsub:
            logger.error("WebSocketManager не инициализирован. Невозможно подключить WebSocket.")
            await websocket.close(code=1011) # Внутренняя ошибка сервера
            return

        await websocket.accept()
        channel_name = await self._get_channel_name(task_id)

        if task_id not in self.active_connections or not self.active_connections[task_id]:
            self.active_connections[task_id] = set()
            try:
                await self.pubsub.subscribe(channel_name)
                logger.info(f"WebSocketManager: Подписались на Redis канал '{channel_name}'")
            except Exception as e:
                logger.error(f"WebSocketManager: Ошибка подписки на Redis канал '{channel_name}': {e}")
                await websocket.close(code=1011)
                return
        
        self.active_connections[task_id].add(websocket)
        logger.info(f"WebSocket подключен для task_id: {task_id}. Всего соединений для этой задачи: {len(self.active_connections[task_id])}")

    async def disconnect(self, task_id: str, websocket: WebSocket):
        if task_id in self.active_connections:
            self.active_connections[task_id].remove(websocket)
            logger.info(f"WebSocket отключен для task_id: {task_id}. Осталось соединений: {len(self.active_connections[task_id])}")
            if not self.active_connections[task_id]: # Если для task_id больше нет активных соединений
                del self.active_connections[task_id]
                if self.pubsub:
                    channel_name = await self._get_channel_name(task_id)
                    try:
                        await self.pubsub.unsubscribe(channel_name)
                        logger.info(f"WebSocketManager: Отписались от Redis канала '{channel_name}'")
                    except Exception as e:
                        logger.error(f"WebSocketManager: Ошибка отписки от Redis канала '{channel_name}': {e}")
        else:
            logger.warning(f"Попытка отключить WebSocket для неизвестного task_id: {task_id}")

    async def _listen_redis(self):
        if not self.pubsub:
            logger.error("WebSocketManager: PubSub не инициализирован. Прослушивание Redis невозможно.")
            return

        logger.info("WebSocketManager: Запуск цикла прослушивания Redis...")
        while True:
            try:
                # timeout важен, чтобы цикл мог прерваться, если задача отменена
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    # message['channel'] и message['data'] - это bytes
                    channel_bytes: bytes = message['channel']
                    data_bytes: bytes = message['data']
                    
                    channel_str = channel_bytes.decode('utf-8')
                    task_id_from_channel = channel_str.split(':')[-1]
                    
                    try:
                        payload_str = data_bytes.decode('utf-8')
                        # Сообщение из Redis имеет структуру: {'task_id': ..., 'message': <сообщение для клиента>}
                        payload_from_redis = json.loads(payload_str) 
                        
                        actual_message_for_client = payload_from_redis.get('message')
                        task_id_from_payload = payload_from_redis.get('task_id')

                        if not actual_message_for_client or task_id_from_payload != task_id_from_channel:
                            logger.error(f"WebSocketManager: Некорректное сообщение из Redis или несоответствие task_id: {payload_str}")
                            continue

                        logger.info(f"WebSocketManager: Получено сообщение из Redis для task_id {task_id_from_channel}: {actual_message_for_client}")

                        if task_id_from_channel in self.active_connections:
                            # Отправляем сообщение всем подключенным клиентам для этого task_id
                            # Используем list() для создания копии, чтобы избежать проблем при изменении set во время итерации
                            disconnected_clients = []
                            for ws_client in list(self.active_connections[task_id_from_channel]):
                                try:
                                    await ws_client.send_json(actual_message_for_client)
                                except WebSocketDisconnect:
                                    logger.warning(f"WebSocketManager: Клиент для task {task_id_from_channel} отключился во время отправки.")
                                    disconnected_clients.append(ws_client)
                                except Exception as e_ws_send:
                                    logger.error(f"WebSocketManager: Ошибка отправки сообщения клиенту для task {task_id_from_channel}: {e_ws_send}")
                                    disconnected_clients.append(ws_client) # Также считаем его отключенным
                            
                            # Удаляем отключившихся клиентов
                            for ws_client in disconnected_clients:
                                if ws_client in self.active_connections.get(task_id_from_channel, set()):
                                    await self.disconnect(task_id_from_channel, ws_client) # Используем метод disconnect для корректной отписки от Redis если нужно

                            logger.debug(f"Сообщение отправлено клиентам для задачи {task_id_from_channel}")
                        else:
                            logger.warning(f"Нет активных WebSocket соединений для task_id {task_id_from_channel}, чтобы отправить сообщение.")
                    
                    except json.JSONDecodeError:
                        logger.error(f"WebSocketManager: Не удалось декодировать JSON из сообщения Redis: {data_bytes.decode('utf-8', errors='ignore')}")
                    except Exception as e_proc:
                        logger.error(f"WebSocketManager: Ошибка обработки сообщения из Redis: {e_proc}", exc_info=True)
                
                await asyncio.sleep(0.01) # Небольшая пауза, чтобы дать другим задачам выполниться
            
            except asyncio.CancelledError:
                logger.info("WebSocketManager: Задача прослушивания Redis была отменена.")
                break
            except aioredis.exceptions.ConnectionError as e_conn:
                logger.error(f"WebSocketManager: Ошибка соединения с Redis в слушателе: {e_conn}. Попытка переподключения через 5с...")
                await asyncio.sleep(5)
                # Попытка переинициализировать pubsub и переподписаться на активные каналы
                # Это упрощенный вариант. В продакшене может потребоваться более сложная логика.
                if self.redis_client:
                    logger.info("WebSocketManager: Попытка переинициализации PubSub и переподписки...")
                    self.pubsub = self.redis_client.pubsub()
                    active_task_ids = list(self.active_connections.keys())
                    for tid in active_task_ids:
                        channel = await self._get_channel_name(tid)
                        await self.pubsub.subscribe(channel)
                        logger.info(f"WebSocketManager: Переподписались на канал {channel} после ошибки соединения.")
                else:
                    logger.error("WebSocketManager: Невозможно переинициализировать PubSub, redis_client отсутствует.")
                    break # Выход из цикла, если redis_client отсутствует
            except Exception as e:
                logger.error(f"WebSocketManager: Непредвиденная ошибка в слушателе Redis: {e}", exc_info=True)
                await asyncio.sleep(1) # Предотвращение быстрого цикла при ошибках


    async def send_message_via_redis(self, message_content: Dict[str, Any], task_id: str):
        """
        Публикует сообщение в Redis. Этот метод НЕ ДОЛЖЕН вызываться из Celery напрямую,
        т.к. Celery - синхронный и в другом процессе. Celery должен использовать свой Redis клиент.
        Этот метод может быть полезен, если FastAPI само инициирует сообщение.
        """
        if not self.redis_client:
            logger.error("WebSocketManager: Redis клиент не инициализирован. Невозможно отправить сообщение.")
            return

        channel_name = await self._get_channel_name(task_id)
        # Структура сообщения для Redis Pub/Sub
        payload_to_redis = {
            'task_id': task_id,          # Для маршрутизации и проверки
            'message': message_content   # Фактическое сообщение для WebSocket клиента
        }
        try:
            json_payload = json.dumps(payload_to_redis)
            await self.redis_client.publish(channel_name, json_payload)
            logger.info(f"WebSocketManager: Опубликовано сообщение в Redis канал '{channel_name}': {message_content.get('status')}")
        except TypeError as e_type:
            logger.error(f"WebSocketManager: Ошибка сериализации сообщения для Redis (канал '{channel_name}'): {e_type}. Сообщение: {message_content}")
        except aioredis.exceptions.RedisError as e_redis:
            logger.error(f"WebSocketManager: Ошибка Redis при публикации в канал '{channel_name}': {e_redis}")
        except Exception as e:
            logger.error(f"WebSocketManager: Непредвиденная ошибка при публикации сообщения в Redis для task {task_id} (канал '{channel_name}'): {e}", exc_info=True)

ws_manager = WebSocketManager()