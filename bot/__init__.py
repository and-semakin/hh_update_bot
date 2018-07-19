from typing import Optional, Dict, Any
import os
import re
import logging
import random
import asyncio
import aiopg
import telepot
import telepot.aio
from hh_api import HeadHunterAPI, HeadHunterAuthError
from telepot.aio.loop import MessageLoop
from telepot.namedtuple import ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton

# logging
log = logging.getLogger('hh-update-bot')
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
log.addHandler(ch)

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
no_resumes_available_message = 'Нет ни одного резюме! Добавь резюме (а лучше несколько) на hh.ru и попробуй снова.'
select_resume_message = 'Выбери резюме, которое будем поднимать.'
resume_selected_message = ('Ок, выбранное резюме будет регулярно обновляться каждые четыре часа в течение одной недели '
                           '(не содержимое, а только дата резюме). Через неделю тебе нужно будет написать мне, '
                           'чтобы продолжить поднимать резюме. Я предупрежу тебя. Желаю найти работу мечты!')


async def on_unknown_message(chat_id):
    msg = random.choice(incorrect_message_answers)
    await bot.sendMessage(chat_id, msg)


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
    user = await get_user(int(user_id))

    # unknown user
    if not user:
        log.info(f'Unknown user: {user_id}')
        await create_user(int(user_id))
        await bot.sendMessage(user_id, hello_message, parse_mode='Markdown')
        return

    # known user
    log.info(f'Known user: {user_id}')

    command = msg['text'].lower()

    if command == '/start':
        await bot.sendMessage(user_id, hello_message)
    elif command == '/help':
        # markup = InlineKeyboardMarkup(inline_keyboard=[
        #     [dict(text='Telegram URL', url='https://core.telegram.org/')],
        #     [InlineKeyboardButton(text='Callback - show notification', callback_data='notification')],
        #     [dict(text='Callback - show alert', callback_data='alert')],
        #     [InlineKeyboardButton(text='Callback - edit message', callback_data='edit')],
        #     [dict(text='Switch to using bot inline', switch_inline_query='initial query')],
        # ])
        await on_unknown_message(user_id)  # TODO: fix this
    elif command == '/cancel':
        # cancel wait for token
        user['is_waiting_for_token'] = False
        await update_user(user)
        await bot.sendMessage(user_id, 'Cancelled.')
    else:
        await on_unknown_message(user_id)

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


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with pg_pool.acquire() as conn:
        async with conn.cursor() as cur:
            log.info(f'Getting user with id {user_id}...')
            await cur.execute(
                """
                SELECT * FROM public.user WHERE user_id = %(user_id)s;
                """,
                {'user_id': user_id}
            )
            user = await cur.fetchone()
            if not user:
                return None
            return {
                'user_id': user[0],
                'hh_token': user[1],
                'first_name': user[2],
                'last_name': user[3],
                'email': user[4],
                'is_waiting_for_token': user[5]
            }


async def create_user(user_id: int) -> None:
    async with pg_pool.acquire() as conn:
        async with conn.cursor() as cur:
            log.info(f'Creating user with id {user_id}...')
            await cur.execute(
                """
                INSERT INTO public.user (user_id) VALUES (%(user_id)s);
                """,
                {'user_id': user_id}
            )


async def update_user(user: Dict[str, Any]) -> None:
    async with pg_pool.acquire() as conn:
        async with conn.cursor() as cur:
            assert 'user_id' in user
            assert 'hh_token' in user
            assert 'first_name' in user
            assert 'last_name' in user
            assert 'email' in user
            assert 'is_waiting_for_token' in user

            log.info(f"Updating user with id {user['user_id']}...")

            await cur.execute(
                """
                UPDATE
                    public.user
                SET
                    hh_token=%(hh_token)s,
                    first_name=%(first_name)s,
                    last_name=%(last_name)s,
                    email=%(email)s,
                    is_waiting_for_token=%(is_waiting_for_token)s
                WHERE user_id=%(user_id)s;
                """,
                {
                    **user
                }
            )


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
    async with pg_pool.acquire() as conn:
        async with conn.cursor() as cur:
            log.info("Creating tables...")
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public."user"
                (
                    user_id bigint NOT NULL,
                    hh_token "char",
                    first_name character varying(64) COLLATE pg_catalog."default",
                    last_name character varying(64) COLLATE pg_catalog."default",
                    email character varying(64) COLLATE pg_catalog."default",
                    is_waiting_for_token boolean NOT NULL DEFAULT true,
                    CONSTRAINT user_pkey PRIMARY KEY (user_id)
                )
                WITH (
                    OIDS = FALSE
                )
                TABLESPACE pg_default;
                
                ALTER TABLE public."user"
                    OWNER to postgres;
    
    
    
                CREATE TABLE IF NOT EXISTS public.resume
                (
                    resume_id bigint NOT NULL,
                    user_id bigint NOT NULL,
                    title character varying(128) COLLATE pg_catalog."default" NOT NULL,
                    status "char" NOT NULL,
                    next_publish_at time with time zone NOT NULL,
                    access "char" NOT NULL,
                    CONSTRAINT resume_pkey PRIMARY KEY (resume_id),
                    CONSTRAINT fk_resume_user_id FOREIGN KEY (user_id)
                        REFERENCES public."user" (user_id) MATCH SIMPLE
                        ON UPDATE NO ACTION
                        ON DELETE CASCADE
                )
                WITH (
                    OIDS = FALSE
                )
                TABLESPACE pg_default;
                
                ALTER TABLE public.resume
                    OWNER to postgres;
                """
            )


if __name__ == '__main__':
    # get environment variables
    TOKEN: str = os.environ['BOT_TOKEN']

    bot: telepot.aio.Bot = telepot.aio.Bot(TOKEN)
    answerer: telepot.aio.helper.Answerer = telepot.aio.helper.Answerer(bot)

    loop = asyncio.get_event_loop()

    loop.run_until_complete(postgres_connect())
    loop.run_until_complete(postgres_create_tables())

    loop.create_task(MessageLoop(bot, {'chat': on_chat_message}).run_forever())

    log.info('Listening for messages in Telegram...')

    loop.run_forever()
