import websockets
import asyncio
import json
import aiohttp

async def create_new_task():
    async with aiohttp.ClientSession() as session:
        # Файл лучше открывать непосредственно перед отправкой
        # и убедиться, что он будет закрыт.
        # aiohttp обычно сам управляет файлом, переданным таким образом.
        file_path = "C:\\Users\\miron\\Практикум\\Практическая работа №2\\test.rar"
        
        # Создаем объект FormData явно для лучшего контроля
        form_data = aiohttp.FormData()
        form_data.add_field("charset", "12345cba")
        form_data.add_field("max_length", str(4)) # <--- ВОТ ИЗМЕНЕНИЕ: str(4)
        
        # Добавляем файл
        # Убедись, что файл 'test.rar' действительно существует по этому пути
        try:
            form_data.add_field("rar_file",
                                open(file_path, "rb"),
                                filename="test.rar", # Явное имя файла
                                content_type="application/x-rar-compressed") # Или application/octet-stream
        except FileNotFoundError:
            print(f"ОШИБКА: Файл не найден по пути {file_path}")
            return None # Или обработай ошибку по-другому

        async with session.post(
            "http://localhost:8000/brut_hash",
            data=form_data # Передаем объект FormData
        ) as response:
            if response.status == 200:
                data = await response.json()
                print(f"Задача успешно создана: {data}") # Лог для отладки
                return data["task_id"]
            else:
                print(f"Ошибка создания задачи: {response.status}")
                print(await response.text()) # Показать текст ошибки от сервера
                return None # Или обработай ошибку

async def handle_task(task_id):
    if task_id is None:
        print("Не удалось получить task_id, WebSocket не будет запущен.")
        return

    ws_url = f"ws://localhost:8000/ws/{task_id}"
    print(f"Подключение к WebSocket: {ws_url}")
    try:
        async with websockets.connect(ws_url) as ws:
            print(f"WebSocket-соединение для задачи {task_id} установлено.")
            
            async def receiver():
                try:
                    while True:
                        msg = await ws.recv()
                        print(f"Задача {task_id} (сервер): {msg}")
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"WebSocket для задачи {task_id} закрыт сервером: {e}")
                except Exception as e:
                    print(f"Ошибка в receiver для задачи {task_id}: {e}")

            async def sender():
                # Для этой задачи sender не обязателен, если нет команд от клиента
                # Если он не нужен, можно закомментировать его вызов в asyncio.gather
                # или оставить для будущих расширений.
                # В данном примере лабораторной он, кажется, не используется для фактического управления.
                # Если input() блокирует, то это может мешать receiver().
                # Для неблокирующего ввода нужен более сложный подход.
                # Пока оставим, но имей в виду.
                # while True:
                #     try:
                #         cmd = await asyncio.to_thread(input, f"Команда для {task_id} (pause/resume/cancel): ")
                #         if ws.closed: break
                #         await ws.send(json.dumps({"command": cmd, "task_id": str(task_id)}))
                #     except RuntimeError: # Если event loop закрыт
                #         break
                #     except Exception as e:
                #         print(f"Ошибка в sender для задачи {task_id}: {e}")
                #         break
                pass # Пока sender ничего не делает, чтобы не блокировать

            # Запускаем только receiver, если sender не нужен или вызывает проблемы с input()
            await asyncio.gather(receiver()) 
            # Если sender нужен и input() не проблема:
            # await asyncio.gather(receiver(), sender())

    except ConnectionRefusedError:
        print(f"Не удалось подключиться к WebSocket {ws_url}. Убедись, что сервер запущен и доступен.")
    except websockets.exceptions.InvalidURI:
        print(f"Неверный URI для WebSocket: {ws_url}")
    except Exception as e:
        print(f"Непредвиденная ошибка при подключении/работе WebSocket для задачи {task_id}: {e}")


async def main():
    while True:
        action = input("Действие (new/quit): ")
        if action == "new":
            task_id = await create_new_task()
            if task_id: # Только если task_id получен
                asyncio.create_task(handle_task(str(task_id))) # Передаем task_id как строку
        elif action == "quit":
            print("Выход...")
            break
        else:
            print("Неизвестное действие.")

if __name__ == "__main__":
    # Проверка пути к файлу перед запуском
    # test_rar_path = "C:\\Users\\miron\\Практикум\\Практическая работа №2\\test.rar"
    # import os
    # if not os.path.exists(test_rar_path):
    #     print(f"ПРЕДУПРЕЖДЕНИЕ: Файл {test_rar_path} не найден. Создание задачи может не удаться.")
    
    asyncio.run(main())