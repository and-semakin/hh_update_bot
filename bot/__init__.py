from typing import Optional, Dict, List, Any
import os
import re
import logging
import random
import asyncio
import aiopg
import telepot
import telepot.aio
from bot.hh_api import HeadHunterAPI, HeadHunterAuthError
import bot.models
from telepot.aio.loop import MessageLoop

# logging
log = logging.getLogger('hh-update-bot')
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
log.addHandler(ch)

tg_bot: telepot.aio.Bot
pg_pool = None
token_pattern = re.compile(r"^[A-Z0-9]{64}$")


incorrect_message_answers = [
    'Извини, не понимаю. Отправь /help, чтобы увидеть полный список моих команд.',
    'Сложно, не понятно. Отправь /help, чтобы увидеть полный список моих команд.',
    'Я не знаю такой команды. Отправь /help, чтобы увидеть полный список моих команд.',
]

hello_message = ('Привет! Я регулярно (примерно раз в четыре часа) буду поднимать твоё резюме в поиске на hh.ru, '
                 'чтобы его увидело большее количество работодателей. '
                 'И тебе даже не придется платить за это ни рубля! :)\n\n'
                 
                 '<b>Важное замечание</b>\n'
                 'Наверняка ребята из hh.ru не обрадуются, что я предоставляю такие услуги бесплатно, '
                 'ведь они берут за это деньги '
                 '(см. цены <a href="https://hh.ru/applicant/resume_service/renewresume">здесь</a>). '
                 'Поэтому я не могу просто создать "приложение", использующее API hh.ru — его заблокируют. '
                 'Но при этом hh.ru открыто предоставляет пользователям API и не запрещает писать скрипты для '
                 'любых своих целей, которые не противоречат правилам. Поэтому мне нужен твой авторизационный токен, '
                 'чтобы производить обновление резюме от твоего лица. '
                 'Я, конечно, буду использовать этот токен ТОЛЬКО для поднятия твоих резюме в поиске, '
                 'честно-честно, но ты должен понимать, что вообще-то передавать свой авторизационный токен '
                 'третьим лицам — небезопасно. Помни, что ты используешь этого бота на свой страх и риск. '
                 'Кстати, токен в любой момент можно отозвать, нажав на иконку "корзины" напротив токена на hh.ru, '
                 'и я настоятельно рекомендую тебе так и поступить, как только мои услуги станут тебе не нужны. '
                 'Кроме того, мой исходный код (на Python) ты всегда можешь посмотреть здесь: '
                 'https://github.com/BrokeRU/hh_update_bot.\n\n'
                 
                 'Итак, план действий следующий:\n'
                 '1. Авторизоваться на hh.ru;\n'
                 '2. Перейти по ссылке: https://dev.hh.ru/admin;\n'
                 '3. Нажать кнопку "Запросить токен";\n'
                 '4. Скопировать <code>access_token</code> (64 символа) и отправить мне.\n\n'
                 )
help_message = ('/start — приветственное сообщение;\n'
                '/help — список доступных команд;\n'
                '/token — сменить токен для доступа к hh.ru;\n'
                '/cancel — отменить ввод токена;\n'
                '/resumes — получить список доступных резюме;\n'
                '/active — получить список продвигаемых резюме.'
                )
new_token_message = ('Отправь мне токен для доступа к hh.ru. Напоминаю, что токен можно взять отсюда: '
                     'https://dev.hh.ru/admin. Если передумал, то отправь /cancel.')
new_token_cancel_message = 'Установка нового токена отменена.'
token_incorrect_message = 'Неправильный токен. Ты уверен, что скопировал всё правильно?'
no_resumes_available_message = 'Нет ни одного резюме! Добавь резюме (а лучше несколько) на hh.ru и попробуй снова.'
select_resume_message = 'Выбери одно или несколько резюме, которые будем продвигать в поиске.\n\n'
resume_selected_message = ('Ок, резюме <b>"{title}"</b> будет регулярно подниматься в поиске каждые четыре часа в '
                           'течение одной недели. Через неделю тебе нужно будет написать мне, '
                           'чтобы продолжить поднимать резюме. Я предупрежу тебя. Желаю найти работу мечты!')
active_resumes_message = 'Продвигаемые резюме:\n\n'
resume_not_found_message = 'Резюме не найдено.'
resume_deactivated_message = 'Резюме больше не будет подниматься в поиске.'


async def send_message(chat_id, message):
    await tg_bot.sendMessage(chat_id, message, parse_mode='HTML')


async def on_unknown_message(chat_id):
    msg = random.choice(incorrect_message_answers)
    await send_message(chat_id, msg)


async def on_chat_message(msg):
    content_type, chat_type, user_id = telepot.glance(msg)
    log.info(f"Chat: {content_type}, {chat_type}, {user_id}")
    log.info(msg)

    # answer in private chats only
    if chat_type != 'private':
        return

    # answer for text messages only
    if content_type != 'text':
        return await on_unknown_message(user_id)

    # check if user is new
    user = await bot.models.TelegramUser.get(int(user_id))

    # unknown user
    if not user:
        log.info(f'Unknown user: {user_id}')
        user = bot.models.TelegramUser(
            user_id=int(user_id)
        )
        await user.create()
        await send_message(user_id, hello_message)
        return

    # known user
    log.info(f'Known user: {user_id}')

    command = msg['text'].lower()

    if command == '/start':
        await send_message(user_id, hello_message)
    elif command == '/help':
        await send_message(user_id, help_message)
    elif command == '/token':
        # wait for token
        user.is_waiting_for_token = True
        await user.update()
        await send_message(user_id, new_token_message)
    elif command == '/cancel':
        # cancel waiting for token
        user.is_waiting_for_token = False
        await user.update()
        await send_message(user_id, new_token_cancel_message)
    elif command == '/resumes':
        await get_resume_list(user)
    elif command == '/active':
        await get_active_resume_list(user)
    elif command.startswith('/resume_'):
        resume_id = command.split('_')[1]
        await activate_resume(user, resume_id)
    elif command.startswith('/deactivate_'):
        resume_id = command.split('_')[1]
        await deactivate_resume(user, resume_id)
    elif user.is_waiting_for_token:
        token = msg['text'].upper()
        await save_token(user, token)
    else:
        await on_unknown_message(user_id)


async def activate_resume(user: bot.models.TelegramUser, resume_id: str) -> None:
    assert user.user_id
    assert user.hh_token

    user_id = user.user_id
    hh_token = user.hh_token

    resume: bot.models.HeadHunterResume

    try:
        async with await HeadHunterAPI.create(hh_token) as api:
            resume = await api.get_resume(resume_id)
    except HeadHunterAuthError:
        await send_message(user_id, token_incorrect_message)

    # set user_id
    resume.user_id = user_id

    await resume.activate()
    await send_message(user_id, resume_selected_message.format(title=resume.title))


async def deactivate_resume(user: bot.models.TelegramUser, resume_id: str) -> None:
    assert user.user_id

    user_id = user.user_id

    resume: bot.models.HeadHunterResume = await bot.models.HeadHunterResume.get(resume_id)

    if resume:
        await resume.deactivate()
        await send_message(user_id, resume_deactivated_message)
    else:
        await send_message(user_id, resume_not_found_message)


async def get_active_resume_list(user: bot.models.TelegramUser) -> None:
    assert user.user_id

    user_id = user.user_id

    active_resumes = await bot.models.HeadHunterResume.get_active_resume_list(user)

    msg = active_resumes_message
    if active_resumes:
        msg += '\n\n'.join(f'<b>{r.title}</b>\n/deactivate_{r.resume_id}' for r in active_resumes)
    else:
        msg += '<b>Список пуст!</b>'

    await send_message(user_id, msg)


async def save_token(user: bot.models.TelegramUser, hh_token: str) -> None:
    assert user.user_id

    user_id = user.user_id

    if not token_pattern.match(hh_token):
        # token mismatched pattern
        log.info(f'Token for chat {user_id} NOT matched pattern: {hh_token}')
        await send_message(user_id, token_incorrect_message)
        return

    log.info(f'Token for chat {user_id} matched pattern.')

    # create API object
    try:
        async with await HeadHunterAPI.create(hh_token) as api:
            # update user object
            user.hh_token = hh_token
            user.is_waiting_for_token = False
            user.first_name = api.first_name
            user.last_name = api.last_name
            user.email = api.email
            await user.update()
    except HeadHunterAuthError:
        await send_message(user_id, token_incorrect_message)
        return

    await get_resume_list(user)


async def get_resume_list(user: bot.models.TelegramUser) -> None:
    assert user.user_id
    assert user.hh_token

    user_id = user.user_id
    hh_token = user.hh_token

    log.info(f'Get resume list for user: {user_id}, token: {hh_token}')

    try:
        async with await HeadHunterAPI.create(hh_token) as api:
            # get resume list
            resumes: List[bot.models.HeadHunterResume] = await api.get_resume_list()

            if resumes:
                msg = select_resume_message
                msg += '\n\n'.join(f'<b>{r.title}</b>\n/resume_{r.resume_id}' for r in resumes)
                await send_message(user_id, msg)
            else:
                # no available resumes
                await send_message(user_id, no_resumes_available_message)
    except HeadHunterAuthError:
        await send_message(user_id, token_incorrect_message)
        return


async def postgres_connect() -> None:
    global pg_pool

    log.info("Connecting to PostgreSQL...")

    # get environment variables
    PG_HOST: str = os.environ['POSTGRES_HOST']
    PG_PORT: str = os.environ['POSTGRES_PORT']
    PG_DB: str = os.environ['POSTGRES_DB']
    PG_USER: str = os.environ['POSTGRES_USER']
    PG_PASSWORD: str = os.environ['POSTGRES_PASSWORD']

    # see: https://www.postgresql.org/docs/current/static/libpq-connect.html#LIBPQ-CONNSTRING
    dsn: str = f'dbname={PG_DB} user={PG_USER} password={PG_PASSWORD} host={PG_HOST} port={PG_PORT}'

    pg_pool = await aiopg.create_pool(dsn)


async def postgres_create_tables() -> None:
    await bot.models.TelegramUser.create_table()
    await bot.models.HeadHunterResume.create_table()


async def main():
    global tg_bot

    # get environment variables
    TOKEN: str = os.environ['BOT_TOKEN']

    tg_bot = telepot.aio.Bot(TOKEN)

    loop = asyncio.get_event_loop()

    await postgres_connect()
    await postgres_create_tables()

    loop.create_task(MessageLoop(tg_bot, {'chat': on_chat_message}).run_forever())

    log.info('Listening for messages in Telegram...')
