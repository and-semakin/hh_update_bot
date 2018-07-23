import asyncio
import bot

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(bot.main())
    loop.run_forever()
