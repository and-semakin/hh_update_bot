import asyncio
import bot.touch
import sys

if __name__ == '__main__':
    loop = asyncio.get_event_loop()

    if len(sys.argv) > 1 and sys.argv[1] == 'touch':
        loop.create_task(bot.touch.main())
    else:
        loop.create_task(bot.main())

    loop.run_forever()
