"""Webhook-mode test: Telegram-style POST to /tg/<path> with secret header reaches the dispatcher (no network)."""
import asyncio, os, tempfile, datetime as dt, sys
os.environ["PUBLIC_URL"] = "https://example.test"; os.environ["PORT"] = "18089"
import aiohttp
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Chat
import bot as B

TOKEN = "123456:TEST_TOKEN_abc"
SENT = []


class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        SENT.append(method)
        return Message(message_id=1, date=dt.datetime.now(), chat=Chat(id=1, type="private"))

    async def stream_content(self, *a, **k):
        yield b""

    async def close(self):
        pass


async def run():
    dp = B.setup(os.path.join(tempfile.mkdtemp(), "t.db"))
    bot = Bot(TOKEN, session=FakeSession())
    runner = await B.start_web(bot, TOKEN, dp)
    url = "http://127.0.0.1:18089" + B.webhook_path(TOKEN)
    upd = {"update_id": 1, "message": {"message_id": 5, "date": 1700000000, "text": "/start",
           "chat": {"id": 777, "type": "private"}, "from": {"id": 777, "is_bot": False, "first_name": "Ali"}}}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}) as r:
            assert r.status in (401, 403), r.status
        async with s.post(url, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": B.webhook_secret(TOKEN)}) as r:
            assert r.status == 200, r.status
        await asyncio.sleep(0.5)
        async with s.get("http://127.0.0.1:18089/health") as r:
            assert r.status == 200
    assert any(type(m).__name__ == "SendMessage" for m in SENT), SENT
    assert B.db.is_owner(777)
    await runner.cleanup()
    print("WEBHOOK TEST PASSED")

asyncio.run(run())
