from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///data/pufferpantry.db"
    anthropic_api_key: str = ""

    # Claude model ids. Never hardcode these at a call site — an id repeated in
    # several places drifts out of sync, and a benchmark run can't record what
    # it actually ran against. Override per-environment in .env.
    #
    # Two roles, because they have genuinely different needs:
    #   extraction — reading a recipe off a photo; the hardest vision task here
    #   fast       — pantry scans and URL imports; cheaper, higher volume
    claude_model_extraction: str = "claude-opus-5"
    claude_model_fast: str = "claude-sonnet-4-20250514"

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
