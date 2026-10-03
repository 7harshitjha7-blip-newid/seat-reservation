from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Seat Reservation System"

    database_url: str

    db_pool_size: int = 10
    db_max_overflow: int = 20

    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # redis_url: str

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()