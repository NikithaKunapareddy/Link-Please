import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_key: str = ""
    mock_api_base_url: str = "https://pseudogram-api.onrender.com"
    database_path: str = "./nik.db"
    host: str = "0.0.0.0"
    port: int = 8000
    max_retries: int = 5
    reconciler_interval_seconds: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
