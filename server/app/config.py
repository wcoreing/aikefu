from functools import lru_cache

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    _env_path = str(Path(__file__).resolve().parents[1] / ".env")
    model_config = SettingsConfigDict(
        env_file=_env_path,
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
    wecom_contact_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WECOM_CONTACT_SECRET", "wecom_contact_secret"
        ),
        description="客户联系 Secret；空则使用 WECOM_CORP_SECRET（需具备客户联系权限）",
    )
    wecom_agent_id: int = Field(
        default=0,
        validation_alias=AliasChoices("WECOM_AGENT_ID", "wecom_agent_id"),
        description="自建应用 AgentId（整数），用于 message/send 通知成员",
    )
    wecom_notify_touser: str = Field(
        default="",
        validation_alias=AliasChoices("WECOM_NOTIFY_TOUSER", "wecom_notify_touser"),
        description="接收高意向提醒的成员 userid，多个用 |",
    )
    wecom_kf_default_servicer_userid: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WECOM_KF_DEFAULT_SERVICER_USERID", "wecom_kf_default_servicer_userid"
        ),
        description="转人工时指定接待成员 userid；空则转待接入池 service_state=2",
    )
    wecom_default_open_kfid: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WECOM_DEFAULT_OPEN_KFID", "wecom_default_open_kfid"
        ),
        description="群发、默认发消息使用的客服账号 open_kfid",
    )
    wecom_group_send_interval_sec: float = Field(
        default=0.25,
        validation_alias=AliasChoices(
            "WECOM_GROUP_SEND_INTERVAL_SEC", "wecom_group_send_interval_sec"
        ),
        description="群发 kf/send_msg 间隔，降频",
    )
    wecom_group_api_interval_sec: float = Field(
        default=0.05,
        validation_alias=AliasChoices(
            "WECOM_GROUP_API_INTERVAL_SEC", "wecom_group_api_interval_sec"
        ),
        description="客户联系 API 调用间隔",
    )
    wecom_group_max_recipients: int = Field(
        default=500,
        validation_alias=AliasChoices(
            "WECOM_GROUP_MAX_RECIPIENTS", "wecom_group_max_recipients"
        ),
        description="单次群发最大客户数上限",
    )
    wecom_group_follow_userids: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WECOM_GROUP_FOLLOW_USERIDS", "wecom_group_follow_userids"
        ),
        description="仅扫描这些成员下的客户（逗号分隔 userid）；空则扫描全部客户联系成员",
    )

    # 百炼 DashScope 应用
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "dashscope_api_key"),
    )
    bailian_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("BAILIAN_APP_ID", "bailian_app_id"),
        description="百炼应用 APP_ID（客服侧：填「工作流应用」或纯 Agent 应用 ID）",
    )
    bailian_invoke_mode: Literal["workflow", "agent"] = Field(
        default="agent",
        validation_alias=AliasChoices("BAILIAN_INVOKE_MODE", "bailian_invoke_mode"),
        description="工作流应用须用 workflow；智能体应用须用 agent。混用会报 Prompt/query 等参数缺失",
    )
    bailian_agent_prompt_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "BAILIAN_AGENT_PROMPT_KEY", "bailian_agent_prompt_key"
        ),
        description="agent 模式额外写入 input 的第三字段名（空则只发 prompt+Prompt）；仅当画布要求其它变量名时填写",
    )
    bailian_workflow_query_key: str = Field(
        default="query",
        validation_alias=AliasChoices(
            "BAILIAN_WORKFLOW_QUERY_KEY", "bailian_workflow_query_key"
        ),
        description="工作流开始节点预置「用户问题」变量名（常见为 query）",
    )
    bailian_workflow_session_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "BAILIAN_WORKFLOW_SESSION_KEY", "bailian_workflow_session_key"
        ),
        description="开始节点若有会话变量则填写（如 session_id）；空则不写入 input",
    )
    bailian_workflow_user_key: str = Field(
        default="external_userid",
        validation_alias=AliasChoices(
            "BAILIAN_WORKFLOW_USER_KEY", "bailian_workflow_user_key"
        ),
        description="工作流开始节点：企微 external_userid 对应变量名",
    )
    bailian_workflow_open_kfid_key: str = Field(
        default="open_kfid",
        validation_alias=AliasChoices(
            "BAILIAN_WORKFLOW_OPEN_KFID_KEY", "bailian_workflow_open_kfid_key"
        ),
        description="开始节点：客服 open_kfid 变量名；空则不写入",
    )
    bailian_workflow_summary_key: str = Field(
        default="summary",
        validation_alias=AliasChoices(
            "BAILIAN_WORKFLOW_SUMMARY_KEY", "bailian_workflow_summary_key"
        ),
        description="开始节点：summary 变量名；空则不写入",
    )
    bailian_base_url: str = Field(
        default="https://dashscope.aliyuncs.com",
        description="北京地域默认；国际版用 https://dashscope-intl.aliyuncs.com",
    )
    bailian_http_timeout_sec: float = Field(default=60.0)

    bailian_group_app_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "BAILIAN_GROUP_APP_ID", "bailian_group_app_id"
        ),
        description="群发「极简工作流」应用 ID；空则内部接口不可用",
    )
    bailian_group_tag_key: str = Field(
        default="tag",
        validation_alias=AliasChoices(
            "BAILIAN_GROUP_TAG_KEY", "bailian_group_tag_key"
        ),
    )
    bailian_group_content_key: str = Field(
        default="content",
        validation_alias=AliasChoices(
            "BAILIAN_GROUP_CONTENT_KEY", "bailian_group_content_key"
        ),
    )

    internal_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("INTERNAL_API_TOKEN", "internal_api_token"),
        description="非空时开放 /internal/*，请求头 X-Internal-Token 校验",
    )

    # Redis：会话 cursor、msg 去重、百炼 session_id；不配置则内存降级（单进程）
    redis_url: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
        description="redis://localhost:6379/0",
    )

    # 回调 POST 是否直接返回明文 success（多数微信客服接入可用）；若后台要求密文可改为 False 并走加密回包
    wecom_plain_success_response: bool = Field(default=True)

    log_level: str = Field(default="INFO")

    # MCP（供百炼工作流/智能体调用）
    mcp_transport: Literal["streamable-http", "sse", "stdio"] = Field(
        default="streamable-http",
        validation_alias=AliasChoices("MCP_TRANSPORT", "mcp_transport"),
        description="MCP 传输：streamable-http（推荐，配置 URL）、sse、stdio（子进程）",
    )
    mcp_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("MCP_HOST", "mcp_host"),
    )
    mcp_port: int = Field(
        default=9999,
        validation_alias=AliasChoices("MCP_PORT", "mcp_port"),
    )
    mcp_path: str = Field(
        default="/mcp",
        validation_alias=AliasChoices("MCP_PATH", "mcp_path"),
        description="streamable-http path",
    )
    mcp_json_response: bool = Field(
        default=True,
        validation_alias=AliasChoices("MCP_JSON_RESPONSE", "mcp_json_response"),
        description="启用 JSON-only 响应（放宽 Accept 头要求）",
    )
    mcp_stateless_http: bool = Field(
        default=True,
        validation_alias=AliasChoices("MCP_STATELESS_HTTP", "mcp_stateless_http"),
        description="启用无状态 HTTP（每个请求独立，无需维护会话）",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
