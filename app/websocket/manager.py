# app/websocket/manager.py (исправленная)
from typing import Dict, Set
from fastapi import WebSocket
import redis.asyncio as redis
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.redis = None
        self.pubsub = None
        self.listener_task = None

    async def initialize(self):
        self.redis = redis.Redis(host='localhost', port=6379)
        self.pubsub = self.redis.pubsub()
        self.listener_task = asyncio.create_task(self.listen_redis())

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = set()
            await self.pubsub.subscribe(f'ws:{task_id}')
            logger.info(f"Подписались на канал ws:{task_id}")
        
        self.active_connections[task_id].add(websocket)
        logger.info(f"WebSocket подключен для задачи {task_id}")

    async def disconnect(self, task_id: str, websocket: WebSocket):
        self.active_connections[task_id].remove(websocket)
        if not self.active_connections[task_id]:
            del self.active_connections[task_id]
            await self.pubsub.unsubscribe(f'ws:{task_id}')
            logger.info(f"Отписались от канала ws:{task_id}")

    async def listen_redis(self):
        while True:
            try:
                message = await self.pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0
                )
                if message:
                    data = json.loads(message['data'])
                    task_id = data['task_id']
                    if task_id in self.active_connections:
                        for websocket in self.active_connections[task_id]:
                            await websocket.send_json(data['message'])
            except Exception as e:
                logger.error(f"Ошибка в Redis listener: {e}")
                await asyncio.sleep(1)

    async def send_message(self, message: dict, task_id: str):
        try:
            await self.redis.publish(
                f'ws:{task_id}',
                json.dumps({'task_id': task_id, 'message': message})
            )
            logger.debug(f"Отправлено сообщение в ws:{task_id}: {message}")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")

ws_manager = WebSocketManager()