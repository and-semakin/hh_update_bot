from typing import Dict, List, NamedTuple, Optional, Any
from datetime import datetime, timedelta
import bot

ResumeID = str
"""Идентификатор резюме на hh.ru."""

UserID = int
"""Идентификатор пользователя Telegram."""


class HeadHunterResume(NamedTuple):
    """Резюме на hh.ru."""

    resume_id: ResumeID
    """Идентификатор резюме."""

    title: str
    """Название резюме."""

    status: str
    """Статус резюме."""

    next_publish_at: datetime
    """Время, когда резюме можно будет поднять в поиске в следующий раз."""

    access: str
    """Доступ к резюме для других пользователей hh.ru."""

    user_id: UserID = None
    """Идентификатор пользователя."""

    is_active: bool = False
    """Активно ли резюме."""

    until: datetime = None
    """До какого срока активно резюме."""

    def as_dict(self):
        return dict(
            resume_id=self.resume_id,
            title=self.title,
            status=self.status,
            next_publish_at=self.next_publish_at,
            access=self.access,
            user_id=self.user_id,
            is_active=self.is_active,
            until=self.until
        )

    @staticmethod
    async def create_table() -> None:
        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                bot.log.info("Models: Creating table 'public.resume'...")
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.resume
                    (
                        resume_id character varying(64) COLLATE pg_catalog."default" NOT NULL,
                        user_id bigint NOT NULL,
                        title character varying(128) COLLATE pg_catalog."default" NOT NULL,
                        status character varying(64) COLLATE pg_catalog."default" NOT NULL,
                        next_publish_at timestamp with time zone NOT NULL,
                        access character varying(64) COLLATE pg_catalog."default" NOT NULL,
                        is_active boolean NOT NULL DEFAULT false,
                        until timestamp with time zone NOT NULL,
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

    async def create(self) -> None:
        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                bot.log.info(f"Models: Inserting resume {self.resume_id}...")

                await cur.execute(
                    """
                    INSERT INTO
                        public.resume
                        (resume_id, title, status, next_publish_at, access, user_id, is_active, until)
                    VALUES
                        (
                            %(resume_id)s,
                            %(title)s,
                            %(status)s,
                            %(next_publish_at)s,
                            %(access)s,
                            %(user_id)s,
                            %(is_active)s,
                            %(until)s
                        );
                    """,
                    self.as_dict()
                )

    @staticmethod
    async def get(resume_id: ResumeID) -> Optional['HeadHunterResume']:
        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                bot.log.info(f'Models: Getting resume with id {self.resume_id}...')
                await cur.execute(
                    """
                    SELECT
                        resume_id,
                        user_id,
                        title,
                        status,
                        next_publish_at,
                        access,
                        is_active,
                        until
                    FROM
                        public.resume
                    WHERE
                        resume_id = %(resume_id)s;
                    """,
                    {'resume_id': resume_id}
                )
                resume = await cur.fetchone()
                if not resume:
                    return None
                return HeadHunterResume(
                    resume_id=resume[0],
                    user_id=resume[1],
                    title=resume[2],
                    status=resume[3],
                    next_publish_at=resume[4],
                    access=resume[5],
                    is_active=resume[6],
                    until=resume[7]
                )

    async def update(self) -> None:
        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                bot.log.info(f'Models: Updating resume with id {self.resume_id}...')
                await cur.execute(
                    """
                    UPDATE
                        public.resume
                    SET
                        user_id=%(user_id)s,
                        title=%(title)s,
                        status=%(status)s,
                        next_publish_at=%(next_publish_at)s,
                        access=%(access)s,
                        is_active=%(is_active)s,
                        until=%(until)s
                    WHERE
                        resume_id = %(resume_id)s;
                    """,
                    self.as_dict()
                )

    async def upsert(self) -> None:
        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                bot.log.info(f'Models: Inserting or updating resume with id {self.resume_id}...')
                await cur.execute(
                    """
                    UPDATE
                        public.resume
                    SET
                        user_id=%(user_id)s,
                        title=%(title)s,
                        status=%(status)s,
                        next_publish_at=%(next_publish_at)s,
                        access=%(access)s,
                        is_active=%(is_active)s,
                        until=%(until)s
                    WHERE resume_id=%(resume_id)s;
                    
                    INSERT INTO
                        (resume_id, title, status, next_publish_at, access, user_id, is_active, until)
                        SELECT
                            %(resume_id)s,
                            %(title)s,
                            %(status)s,
                            %(next_publish_at)s,
                            %(access)s,
                            %(user_id)s,
                            %(is_active)s,
                            %(until)s
                        WHERE NOT EXISTS (
                            SELECT
                                1
                            FROM
                                public.resume
                            WHERE
                                resume_id=%(resume_id)s
                        );
                    """,
                    self.as_dict()
                )

    async def activate(self) -> None:
        bot.log.info(f'Models: Activating resume with id {self.resume_id}...')
        self.is_active = True
        self.until = datetime.now() + timedelta(days=7)
        self.upsert()

    async def deactivate(self) -> None:
        bot.log.info(f'Models: Deactivating resume with id {self.resume_id}...')
        self.is_active = False
        self.update()

    @staticmethod
    async def get_active_resume_list(user: Dict[str, Any]) -> List['HeadHunterResume']:
        assert 'user_id' in user

        async with bot.pg_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        resume_id,
                        user_id,
                        title,
                        status,
                        next_publish_at,
                        access,
                        is_active,
                        until
                    FROM
                        public.resume
                    WHERE
                        user_id=%(user_id)s AND
                        is_active;
                    """,
                    {
                        'user_id': user['user_id']
                    }
                )

                resumes = await cur.fetchall()
                return [
                    HeadHunterResume(
                        resume_id=r[0],
                        user_id=r[1],
                        title=r[2],
                        status=r[3],
                        next_publish_at=r[4],
                        access=r[5],
                        is_active=r[6],
                        until=r[7]
                    )
                    for r in resumes
                ]
