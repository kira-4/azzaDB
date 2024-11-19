import asyncio
from hydrogram import Client


api_hash = "164c0237d988d53ad1538adba88196ff"
api_id = 20655834

bot = Client("ret_mes", api_id=api_id, api_hash=api_hash)

chat_id = -1001869042104


async def get_history(chat):
    # chat_info = await bot.get_chat(chat)
    # print(f"chat info {chat_info}")
    async for message in bot.get_chat_history(chat, limit=200):
        if message.text:
            # print(fr"{message.text}, {message.id}")
            text = message.text.replace("\n", "\\n")
            with open("data.csv", "a") as f:
                f.write(f"{text}, {message.id}\n")


async def main():
    await bot.start()
    await get_history(chat_id)
    await bot.stop()

asyncio.run(main())
