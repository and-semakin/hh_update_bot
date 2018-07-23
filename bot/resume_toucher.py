import os
import logging
import asyncio
import aiopg
from .hh_api import HeadHunterAPI, HeadHunterResume, HeadHunterAuthError, HeadHunterResumeUpdateError

# logging
log = logging.getLogger('hh-update-bot')
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
log.addHandler(ch)

pg_pool = None


async def touch_ready_resumes() -> None:
    async with pg_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    resume_id, title, status, next_publish_at, access, hh_token
                FROM
                    public.resume
                JOIN
                    public.user ON public.user.user_id = public.resume.user_id
                WHERE
                    next_publish_at < NOW();
                """
            )

            resumes = {}

            for r in await cur.fetchall():
                hh_token = r[5]
                if hh_token not in resumes:
                    resumes[hh_token] = []

                resumes[hh_token].append(
                    HeadHunterResume(
                        id=r[0],
                        title=r[1],
                        status=r[2],
                        next_publish_at=r[3],
                        access=r[4]
                    )
                )

            for hh_token, user_resumes in resumes.items():
                try:
                    async with await HeadHunterAPI.create(hh_token) as api:
                        for r in user_resumes:
                            try:
                                has_updated, r = await api.touch_resume(r)
                                if has_updated:
                                    log.info(f'Resume updated: {r.title} ({r.id})')

                                else:
                                    log.info(f'Too often: {r.title} ({r.id})')
                            except HeadHunterResumeUpdateError:
                                log.info(f'Error updating resume: {r.title} ({r.id})')
                except HeadHunterAuthError:
                    log.info(f'Wrong token: {hh_token}')
                    continue


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

if __name__ == '__main__':
    loop = asyncio.get_event_loop()

    log.info('Updating resumes in HH...')
    loop.run_until_complete(postgres_connect())
    loop.run_until_complete(touch_ready_resumes())
