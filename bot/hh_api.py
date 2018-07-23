from typing import Dict, List, Tuple
from aiohttp.client import ClientSession
import dateutil.parser
import bot.models

APIToken = str


class HeadHunterAuthError(Exception):
    """Ошибка авторизации в API hh.ru."""


class HeadHunterResumeUpdateTooOftenError(Exception):
    """Слишком частое обновление резюме в API hh.ru."""


class HeadHunterResumeUpdateError(Exception):
    """Ошибка обновления резюме в API hh.ru.

    Возможные причины:
    * не заполнены обязательные поля,
    * не отредактированы поля после блокировки модератором,
    * резюме находится на проверке у модератора."""


class HeadHunterAPI:
    """API для hh.ru.

    См. https://github.com/hhru/api"""

    api_url: str = 'https://api.hh.ru'

    api_token: APIToken
    headers: Dict[str, str]
    session: ClientSession

    first_name: str
    last_name: str
    email: str

    @classmethod
    async def create(cls, api_token: APIToken) -> 'HeadHunterAPI':
        """Метод, создающий новый объект API hh.ru.

        :param api_token: токен для API; можно взять отсюда: https://dev.hh.ru/admin?new-token=true
        :raise HeadHunterAuthError: если произошла ошибка авторизации
        :return: объект типа HeadHunterAPI с данными о пользователе API
        """
        api = HeadHunterAPI()
        api.api_token = api_token
        api.headers = {'Authorization': f'Bearer {api_token}'}
        api.session = ClientSession(headers=api.headers)
        try:
            await api.get_user_data()
        except HeadHunterAuthError:
            await api.session.close()
            raise

        return api

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, exc_traceback):
        await self.session.close()

    async def get_user_data(self) -> None:
        """Метод, получающий данные о пользователе API.

        См. https://github.com/hhru/api/blob/master/docs/me.md

        :raise HeadHunterAuthError: если произошла ошибка авторизации
        :return: None
        """
        async with self.session.get(f'{self.api_url}/me') as resp:
            if resp.status != 200:
                raise HeadHunterAuthError
            data = await resp.json()
            self.first_name = data['first_name']
            self.last_name = data['last_name']
            self.email = data['email']

    async def get_resume(self, resume_id: bot.models.ResumeID) -> bot.models.HeadHunterResume:
        """

        :param resume_id:
        :return:
        """
        async with self.session.get(f'{self.api_url}/resumes/{resume_id}') as resp:
            if resp.status != 200:
                raise HeadHunterAuthError
            data = await resp.json()

            return bot.models.HeadHunterResume(
                resume_id=data['id'],
                title=data['title'],
                status=data['status']['id'],
                access=data['access']['type']['id'],
                next_publish_at=dateutil.parser.parse(data['next_publish_at'])
            )

    async def get_resume_list(self) -> List[bot.models.HeadHunterResume]:
        """Метод, возвращающий список резюме пользователя API.

        См. https://github.com/hhru/api/blob/master/docs/resumes.md#mine

        :raise HeadHunterAuthError: если произошла ошибка авторизации
        :return:
        """
        async with self.session.get(f'{self.api_url}/resumes/mine') as resp:
            if resp.status != 200:
                raise HeadHunterAuthError
            data = await resp.json()

            return [
                await self.get_resume(item['id'])
                for item in data['items']
            ]

    async def touch_resume(self, resume: bot.models.HeadHunterResume) -> Tuple[bool, bot.models.HeadHunterResume]:
        """Метод, обновляющий время на указанном резюме.

        См. https://github.com/hhru/api/blob/master/docs/resumes.md#publish

        :param resume: резюме для обновления
        :raise HeadHunterAuthError: если произошла ошибка авторизации
        :raise HeadHunterResumeUpdateError: если невозможно опубликовать резюме
        :return: было ли резюме обновлено и новый объект резюме
        """
        async with self.session.post(f'{self.api_url}/resumes/{resume.id}/publish') as resp:
            if resp.status == 403:
                raise HeadHunterAuthError
            elif resp.status == 400:
                raise HeadHunterResumeUpdateError
            elif resp.status == 429:
                return False, await self.get_resume(resume.id)

            return True, await self.get_resume(resume.id)
