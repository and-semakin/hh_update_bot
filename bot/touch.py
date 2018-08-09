import logging
import asyncio
import datetime
import bot
from bot.hh_api import HeadHunterAPI, HeadHunterAuthError, HeadHunterResumeUpdateError
from bot.models import HeadHunterResume

# logging
log = logging.getLogger('hh-update-bot')
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
log.addHandler(ch)

pg_pool = None


resume_timed_out_message = 'Продвижение твоего резюме было автоматически прекращено.'


async def touch_ready_resumes() -> None:
    resumes_and_users = await HeadHunterResume.get_active_resume_list(pg_pool)

    for user_id, user_resumes in resumes_and_users.items():
        for r in user_resumes:
            user = r['user']
            resume = r['resume']

            try:
                async with await HeadHunterAPI.create(user.hh_token) as api:
                    if resume.until < datetime.datetime.now():
                        # notify user and deactivate resume
                        await bot.send_message(user.user_id, resume_timed_out_message)
                        await resume.deactivate(pg_pool)
                    try:
                        has_updated, resume = await api.touch_resume(resume)
                        if has_updated:
                            log.info(f'Resume updated: {resume.title} ({resume.resume_id})')
                            resume.update()
                        else:
                            log.info(f'Too often: {resume.title} ({resume.resume_id})')
                    except HeadHunterResumeUpdateError:
                        log.info(f'Error updating resume: {resume.title} ({resume.resume_id})')
            except HeadHunterAuthError:
                log.info(f'Wrong token: {user.hh_token}')
                continue


async def main():
    loop = asyncio.get_event_loop()

    log.info('Updating resumes in HH...')
    asyncio.run(bot.postgres_connect())
    print('pg_pool', pg_pool, type(pg_pool))
    print('bot.pg_pool', bot.pg_pool, type(bot.pg_pool))
    asyncio.run(touch_ready_resumes())
