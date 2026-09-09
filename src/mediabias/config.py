from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA = Path(__file__).parent / 'data'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='MEDIABIAS_', env_file='.env', extra='ignore')
    mode: Literal['demo', 'live'] = 'demo'
    database_url: str = 'sqlite:///./var/mediabias.db'
    # Map secret tokens to server-controlled identities and roles.
    users: dict[str, dict[str, str]] = Field(default_factory=dict)
    llm_base_url: str = 'https://api.openai.com/v1'
    llm_api_key: str = ''
    llm_model: str = ''
    verifier_model: str = ''
    llm_concurrency: int = Field(2, ge=1, le=16)
    http_concurrency: int = Field(5, ge=1, le=16)
    poll_seconds: int = Field(600, ge=60)
    sources_path: Path = DATA / 'sources.json'
    active_source_ids: list[str] = Field(default_factory=list)
    max_article_chars: int = Field(10000, ge=1000, le=30000)
    max_event_articles: int = Field(25, ge=2, le=60)

    @model_validator(mode='after')
    def secure_live(self):
        if self.mode == 'live' and not self.users:
            raise ValueError('Live mode requires MEDIABIAS_USERS with reviewer/editor tokens.')
        identities = set()
        for token, user in self.users.items():
            if len(token) < 24 or user.get('role') not in {'reviewer', 'editor'} or not user.get('id'):
                raise ValueError('Each token needs 24+ characters, a unique id, and reviewer/editor role.')
            if user['id'] in identities:
                raise ValueError('One token per identity; duplicate identities are not permitted.')
            identities.add(user['id'])
        return self
