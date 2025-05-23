import websockets
import asyncio
import json
import aiohttp

async def create_new_task():
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:8000/brut_hash",
            data={
                "charset": "abc123",
                "max_length": 4,
                "rar_file": open("test.rar", "rb")
            }
        ) as response:
            data = await response.json()
            return data["task_id"]

async def handle_task(task_id):
    async with websockets.connect(f"ws://localhost:8000/ws/{task_id}") as ws:
        async def receiver():
            while True:
                msg = await ws.recv()
                print(f"Задача {task_id}: {msg}")
        
        async def sender():
            while True:
                cmd = input(f"Команда для {task_id} (pause/resume/cancel): ")
                await ws.send(json.dumps({"command": cmd}))
        
        await asyncio.gather(receiver(), sender())

async def main():
    while True:
        action = input("Действие (new/quit): ")
        if action == "new":
            task_id = await create_new_task()
            asyncio.create_task(handle_task(task_id))
        elif action == "quit":
            break

if __name__ == "__main__":
    asyncio.run(main())