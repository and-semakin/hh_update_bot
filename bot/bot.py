import os
import re
import time
import logging
import random
import asyncio
import aiopg
import aiohttp
import telepot
import telepot.aio
from hh_api import HeadHunterAPI, HeadHunterAuthError
from telepot.aio.loop import MessageLoop
from telepot.namedtuple import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, ForceReply
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton

# logging
log = logging.getLogger('hh-update-bot')
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
log.addHandler(ch)

redis = None
token_pattern = re.compile(r"^[A-Z0-9]{64}$")


incorrect_message_answers = [
    'Извини, не понимаю. Отправь /help, чтобы увидеть полный список моих команд.',
    'Сложно, не понятно. Отправь /help, чтобы увидеть полный список моих команд.',
    'Я не знаю такой команды. Отправь /help, чтобы увидеть полный список моих команд.',
]

hello_message = ('Привет! Я регулярно (примерно раз в четыре часа) буду поднимать твоё резюме в поиске на hh.ru, '
                 'чтобы его увидело большее количество работодателей. '
                 'И тебе даже не придется платить за это ни рубля! :)\n\n'
                 
                 '*Важное замечание*\n'
                 'Наверняка ребята из hh.ru не обрадуются, что я предоставляю такие услуги бесплатно, '
                 'ведь они берут за это деньги (см. цены [здесь](https://hh.ru/applicant/resume_service/renewresume)). '
                 'Поэтому я не могу просто создать "приложение", использующее API hh.ru -- его заблокируют. '
                 'Но при этом hh.ru открыто предоставляет пользователям API и не запрещает писать скрипты для '
                 'любых своих целей, которые не противоречат правилам. Поэтому мне нужен твой авторизационный токен, '
                 'чтобы производить обновление резюме от твоего лица. '
                 'Я, конечно, буду использовать этот токен ТОЛЬКО для поднятия твоих резюме в поиске, '
                 'честно-честно, но ты должен понимать, что вообще-то передавать свой авторизационный токен '
                 'третьим лицам -- небезопасно. Помни, что ты используешь этого бота на свой страх и риск. '
                 'Кстати, токен в любой момент можно отозвать, нажав на иконку "корзины" напротив токена на hh.ru, '
                 'и я настоятельно рекомендую тебе так и поступить, как только мои услуги станут тебе не нужны. '
                 'Кроме того, мой исходный код (на Python) ты всегда можешь посмотреть здесь: '
                 'https://github.com/BrokeRU/hh-resume-auto-publish.\n\n'
                 
                 'Итак, план действий следующий:\n'
                 '1. Авторизоваться на hh.ru;\n'
                 '2. Перейти по ссылке: https://dev.hh.ru/admin;\n'
                 '3. Нажать кнопку "Запросить токен";\n'
                 '4. Скопировать `access_token` (64 символа) и отправить мне.\n\n'
                 )
token_incorrect_message = 'Неправильный токен. Ты уверен, что скопировал всё правильно?'
error_getting_resume_list_message = ('Не удалось загрузить список твоих резюме. Одно из двух: либо токен не валиден, '
                                     'либо нет ни одного резюме. Перепроверь и отправь токен ещё раз, пожалуйста.')
select_resume_message = 'Выбери резюме, которое будем поднимать.'
resume_selected_message = ('Ок, выбранное резюме будет регулярно обновляться каждые четыре часа в течение одной недели '
                           '(не содержимое, а только дата резюме). Через неделю тебе нужно будет написать мне, '
                           'чтобы продолжить поднимать резюме. Я предупрежу тебя. Желаю найти работу мечты!')


async def on_unknown_message(chat_id):
    msg = random.choice(incorrect_message_answers)
    await bot.sendMessage(chat_id, msg)


async def get_resume_list(chat_id, token):
    headers = {'Authorization': f'Bearer {token}'}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(resume_list_url) as resp:
            if resp.status != 200:
                log.info(f'Get resume list: error getting resume, status {resp.status}')
                return
            data = await resp.json()
            log.info(f'Got resume list: chat_id {chat_id}, status {resp.status}')
            return data['items'] or False


async def on_chat_message(msg):
    content_type, chat_type, chat_id = telepot.glance(msg)
    log.info(f"Chat: {content_type}, {chat_type}, {chat_id}")
    log.info(msg)

    # answer in private chats only
    if chat_type != 'private':
        return

    # answer for text messages only
    if content_type != 'text':
        return await on_unknown_message(chat_id)

    # user key in Redis
    user_key = f'user:{chat_id}'

    # check if user is new
    known_user = await redis.exists(user_key)
    if known_user:
        log.info(f'Known user: {chat_id}')

        token = msg['text'].upper()
        if token_pattern.match(token):
            log.info(f'Token for chat {chat_id} matched pattern.')
            resumes = await get_resume_list(chat_id, token)
            if resumes:
                # save token to Redis
                await redis.hset(user_key, 'token', token)

                # save resumes to Redis
                for r in resumes:
                    r_key = 'resume:{id}'.format(id=r['id'])
                    await redis.set(r_key, r['title'])

                # send resume list in inline keyboard
                buttons = [
                    [InlineKeyboardButton(text=r['title'], callback_data='select_resume:{0}'.format(r['id']))]
                    for r in resumes
                ]
                markup = InlineKeyboardMarkup(inline_keyboard=buttons)
                message_with_inline_keyboard = await bot.sendMessage(chat_id, select_resume_message,
                                                                     reply_markup=markup)
                # save message ID to Redis
                await redis.hset(user_key, 'message_with_inline_keyboard', message_with_inline_keyboard['message_id'])
                return
            else:
                # error getting resume list: 403 or empty resume list
                await bot.sendMessage(chat_id, error_getting_resume_list_message)
                return
        else:
            # token mismatched pattern
            log.info(f'Token for chat {chat_id} NOT matched pattern: {token}')
            await bot.sendMessage(chat_id, token_incorrect_message)
            return
    else:
        # unknown user
        log.info(f'Unknown user: {chat_id}')
        await bot.sendMessage(chat_id, hello_message, parse_mode='Markdown')
        await redis.hset(user_key, 'hello', '1')  # mark that user has seen hello message
        return

    command = msg['text'].lower()

    if command == '/start':
        await bot.sendMessage(chat_id, hello_message)
    elif command == '/help':
        markup = InlineKeyboardMarkup(inline_keyboard=[
                     [dict(text='Telegram URL', url='https://core.telegram.org/')],
                     [InlineKeyboardButton(text='Callback - show notification', callback_data='notification')],
                     [dict(text='Callback - show alert', callback_data='alert')],
                     [InlineKeyboardButton(text='Callback - edit message', callback_data='edit')],
                     [dict(text='Switch to using bot inline', switch_inline_query='initial query')],
                 ])

        message_with_inline_keyboard = await bot.sendMessage(chat_id, 'Inline keyboard with various buttons', reply_markup=markup)
    elif command == '/redis':
        redis_status = await redis.info()
        markup = ReplyKeyboardRemove()
        await bot.sendMessage(chat_id, redis_status, reply_markup=markup)
    else:
        await on_unknown_message(chat_id)


async def on_callback_query(msg):
    query_id, chat_id, data = telepot.glance(msg, flavor='callback_query')
    log.info(f'Callback query: {query_id}, {chat_id}, {data}')

    if data.startswith('select_resume'):
        r_id = data.split(':')[1]
        r_key = f'resume:{r_id}'
        r_title = (await redis.get(r_key)).decode()
        if not r_title:
            await bot.answerCallbackQuery(query_id, text='Resume not found!')
        else:
            # user key in Redis
            user_key = f'user:{chat_id}'

            # get message id from Redis
            message_with_inline_keyboard = int(await redis.hget(user_key, 'message_with_inline_keyboard'))
            msg_idf = (chat_id, message_with_inline_keyboard)
            log.info('Callback query: ' + str(msg_idf))

            # update message with inline keyboard
            await bot.editMessageText(msg_idf, f'👌 Выбрано резюме: {r_title}')

            # del message id from Redis
            await redis.hdel(user_key, 'message_with_inline_keyboard')

            # save resume id to Redis
            await redis.hset(user_key, 'resume', r_id)
            await redis.hset(user_key, 'last_update', 0)
            update_until = int(time.time()) + 7 * 24 * 60 * 60
            await redis.hset(user_key, 'update_until', update_until)

            # notify user that resume will be updated
            await bot.sendMessage(chat_id, resume_selected_message)
            await bot.sendSticker(chat_id, 'CAADAgADow0AAlOx9wMSX5-GZpBRAAEC')


async def connect_redis():
    global redis
    REDIS_URI = os.environ['REDIS_URI']
    REDIS_PORT = os.environ['REDIS_PORT']
    redis = await aioredis.create_redis(
        (REDIS_URI, REDIS_PORT), loop=loop)


if __name__ == '__main__':
    TOKEN = os.environ['BOT_TOKEN']

    bot = telepot.aio.Bot(TOKEN)
    answerer = telepot.aio.helper.Answerer(bot)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(connect_redis())
    loop.create_task(MessageLoop(bot, {'chat': on_chat_message,
                                       'callback_query': on_callback_query}).run_forever())
    log.info('Listening ...')

    loop.run_forever()
