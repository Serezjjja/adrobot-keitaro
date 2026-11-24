from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_base_url: HttpUrl = HttpUrl("https://keitaro-for-tests.etolegko.com")
    keitaro_api_key: SecretStr = SecretStr("")
    campaign_prefix: str = Field(default="test_", pattern=r"^test_(?:[a-z0-9]+_)*$")
    database_url: str = "sqlite:///./adrobot.db"
