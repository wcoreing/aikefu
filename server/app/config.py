from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = Field(default="0.0.0.0", description="绑定地址")
    port: int = Field(default=8080, description="监听端口")

    # 企微回调（与后台「接收消息」配置一致）
    wecom_token: str = Field(
        default="",
        validation_alias=AliasChoices("WECOM_TOKEN", "wecom_token"),
    )
    wecom_encoding_aes_key: str = Field(
        default="",
        validation_alias=AliasChoices("WECOM_ENCODING_AES_KEY", "wecom_encoding_aes_key"),
        description="EncodingAESKey",
    )
    wecom_corp_id: str = Field(
        default="",
        validation_alias=AliasChoices("WECOM_CORP_ID", "wecom_corp_id"),
        description="企业 CorpID，作 ReceiveId",
    )
    wecom_corp_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WECOM_CORP_SECRET", "wecom_corp_secret"),
        description="可调用微信客服 API 的应用 Secret（需在客服后台授权）",
    )

    # 百炼 DashScope 应用
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "dashscope_api_key"),
    )
    bailian_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("BAILIAN_APP_ID", "bailian_app_id"),
        description="百炼应用 APP_ID",
    )
    bailian_base_url: str = Field(
        default="https://dashscope.aliyuncs.com",
        description="北京地域默认；国际版用 https://dashscope-intl.aliyuncs.com",
    )
    bailian_http_timeout_sec: float = Field(default=60.0)
    bailian_max_retries: int = Field(default=2)

    # Redis：会话 cursor、msg 去重、百炼 session_id；不配置则内存降级（单进程）
    redis_url: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
        description="redis://localhost:6379/0",
    )

    # 回调 POST 是否直接返回明文 success（多数微信客服接入可用）；若后台要求密文可改为 False 并走加密回包
    wecom_plain_success_response: bool = Field(default=True)

    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    return Settings()
