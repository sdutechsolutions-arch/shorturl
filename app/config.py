from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    session_secret: str
    admin_username: str
    admin_password_hash: str
    base_url: str = "https://shorturl.janapriyahomes.com"
    slug_length: int = 6
    session_max_age_seconds: int = 60 * 60 * 24 * 14

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
