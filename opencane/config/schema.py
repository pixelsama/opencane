"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""  # Shared token for bridge auth (optional, recommended)
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    reply_to_message: bool = False  # If true, bot replies quote the triggering message


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""
    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration using Stream mode."""
    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(BaseModel):
    """Discord channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

class EmailConfig(BaseModel):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""
    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = True  # If false, inbound email is read but no automatic reply is sent
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class MochatMentionConfig(BaseModel):
    """Mochat mention behavior configuration."""
    require_in_groups: bool = False


class MochatGroupRule(BaseModel):
    """Mochat per-group mention requirement."""
    require_mention: bool = False


class MochatConfig(BaseModel):
    """Mochat channel configuration."""
    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0  # 0 means unlimited retries
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000


class SlackDMConfig(BaseModel):
    """Slack DM policy configuration."""
    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


class SlackConfig(BaseModel):
    """Slack channel configuration."""
    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)  # Allowed channel IDs if allowlist
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class QQConfig(BaseModel):
    """QQ channel configuration using botpy SDK."""
    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public access)


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""
    workspace: str = "~/.opencane/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    memory_window: int = 50
    max_subagents: int = 4


class AgentsConfig(BaseModel):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""
    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""
    timeout: int = 60


class MCPServerConfig(BaseModel):
    """MCP server connection configuration (stdio or HTTP)."""
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP: streamable HTTP endpoint URL


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class HardwareAuthConfig(BaseModel):
    """Hardware runtime auth configuration."""

    enabled: bool = False
    token: str = ""
    device_auth_enabled: bool = False
    allow_unbound_devices: bool = False
    require_activated_devices: bool = True
    control_api_rate_limit_enabled: bool = True
    control_api_rate_limit_rpm: int = 600
    control_api_rate_limit_burst: int = 120
    control_api_replay_protection_enabled: bool = False
    control_api_replay_window_seconds: int = 300


class HardwareMQTTConfig(BaseModel):
    """MQTT transport configuration for EC600-like devices."""

    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "opencane-hardware"
    keepalive_seconds: int = 30
    reconnect_min_seconds: int = 1
    reconnect_max_seconds: int = 30
    qos_control: int = 1
    qos_audio: int = 0
    up_control_topic: str = "device/+/up/control"
    up_audio_topic: str = "device/+/up/audio"
    down_control_topic_template: str = "device/{device_id}/down/control"
    down_audio_topic_template: str = "device/{device_id}/down/audio"
    replay_enabled: bool = True
    control_replay_window: int = 50
    offline_control_buffer: int = 50
    heartbeat_topic: str = "opencane/hardware/heartbeat"
    heartbeat_interval_seconds: int = 20
    tls_enabled: bool = False


class ControlPlaneConfig(BaseModel):
    """Control-plane remote config client settings."""

    enabled: bool = False
    base_url: str = ""
    api_token: str = ""
    runtime_config_path: str = "/v1/control/runtime_config"
    device_policy_path: str = "/v1/control/device_policy"
    timeout_seconds: float = 3.0
    cache_ttl_seconds: int = 30


class HardwareAudioConfig(BaseModel):
    """Audio pipeline tuning for realtime speech capture."""

    enable_vad: bool = True
    prebuffer_chunks: int = 3
    jitter_window: int = 8
    vad_silence_chunks: int = 6


class HardwareTelemetryConfig(BaseModel):
    """Telemetry normalization and persistence toggles."""

    normalize_enabled: bool = False
    persist_samples_enabled: bool = False


class HardwareToolResultConfig(BaseModel):
    """Device tool result event handling toggles."""

    enabled: bool = False
    mark_device_operation_enabled: bool = True


class HardwareConfig(BaseModel):
    """Hardware runtime server configuration."""

    enabled: bool = False
    adapter: str = "websocket"  # websocket | mock | ec600 | generic_mqtt
    device_profile: str = "generic_v1"
    profile_overrides: dict[str, Any] = Field(default_factory=dict)
    tts_mode: str = "device_text"  # device_text | server_audio
    tts_audio_chunk_bytes: int = 1600
    network_profile: str = "cellular"  # cellular | default
    apply_profile_defaults: bool = True
    strict_startup: bool = False
    host: str = "0.0.0.0"
    port: int = 18791
    control_host: str = "127.0.0.1"
    control_port: int = 18792
    control_max_body_bytes: int = 12 * 1024 * 1024
    heartbeat_seconds: int = 20
    observability_sqlite_path: str = "~/.opencane/data/hardware/observability.db"
    observability_max_samples: int = 4000
    packet_magic: int = 161  # 0xA1
    audio: HardwareAudioConfig = Field(default_factory=HardwareAudioConfig)
    auth: HardwareAuthConfig = Field(default_factory=HardwareAuthConfig)
    mqtt: HardwareMQTTConfig = Field(default_factory=HardwareMQTTConfig)
    telemetry: HardwareTelemetryConfig = Field(default_factory=HardwareTelemetryConfig)
    tool_result: HardwareToolResultConfig = Field(default_factory=HardwareToolResultConfig)
    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)

    def apply_network_profile(self) -> None:
        """Apply non-final tuning defaults for known network profiles."""
        if not self.apply_profile_defaults:
            return
        profile = (self.network_profile or "default").strip().lower()
        if profile != "cellular":
            return
        self.heartbeat_seconds = max(self.heartbeat_seconds, 30)
        self.mqtt.keepalive_seconds = max(self.mqtt.keepalive_seconds, 45)
        self.mqtt.reconnect_min_seconds = max(self.mqtt.reconnect_min_seconds, 2)
        self.mqtt.reconnect_max_seconds = max(self.mqtt.reconnect_max_seconds, 60)
        self.mqtt.heartbeat_interval_seconds = max(self.mqtt.heartbeat_interval_seconds, 30)


class VisionConfig(BaseModel):
    """Vision endpoint and VLM runtime configuration."""

    enabled: bool = True
    model: str = ""
    max_image_bytes: int = 2 * 1024 * 1024
    default_prompt: str = "Describe the scene with key obstacles and safety hints."


class LifelogConfig(BaseModel):
    """Lifelog storage and retrieval configuration."""

    enabled: bool = True
    sqlite_path: str = "~/.opencane/data/lifelog/lifelog.db"
    chroma_persist_dir: str = "~/.opencane/data/lifelog/chroma"
    vector_backend: str = "chroma"  # chroma | qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "lifelog_semantic"
    qdrant_timeout_seconds: float = 3.0
    qdrant_vector_size: int = 64
    embedding_enabled: bool = False
    embedding_model: str = ""
    embedding_timeout_seconds: float = 8.0
    image_asset_dir: str = "~/.opencane/data/lifelog/images"
    image_asset_max_files: int = 5000
    ingest_queue_max_size: int = 64
    ingest_workers: int = 2
    ingest_overflow_policy: str = "reject"  # reject | wait | drop_oldest
    ingest_enqueue_timeout_ms: int = 500
    default_top_k: int = 5
    max_timeline_items: int = 200
    dedup_max_distance: int = 3
    retention_cleanup_on_startup: bool = False
    retention_runtime_events_days: int = 30
    retention_thought_traces_days: int = 30
    retention_device_sessions_days: int = 30
    retention_device_operations_days: int = 30
    retention_telemetry_samples_days: int = 7


class DigitalTaskConfig(BaseModel):
    """Digital task execution configuration."""

    enabled: bool = True
    sqlite_path: str = "~/.opencane/data/digital_task/tasks.db"
    default_timeout_seconds: int = 120
    max_concurrent_tasks: int = 2
    status_retry_count: int = 2
    status_retry_backoff_ms: int = 300


class SafetyConfig(BaseModel):
    """Safety policy configuration for runtime outputs."""

    enabled: bool = True
    low_confidence_threshold: float = 0.55
    max_output_chars: int = 320
    prepend_caution_for_risk: bool = True
    semantic_guard_enabled: bool = True
    directional_confidence_threshold: float = 0.85


class InteractionConfig(BaseModel):
    """Interaction policy for emotion/proactive/silent runtime behavior."""

    enabled: bool = True
    emotion_enabled: bool = True
    proactive_enabled: bool = True
    silent_enabled: bool = True
    low_confidence_threshold: float = 0.45
    high_risk_levels: list[str] = Field(default_factory=lambda: ["P0", "P1"])
    proactive_sources: list[str] = Field(default_factory=lambda: ["vision_reply"])
    silent_sources: list[str] = Field(default_factory=lambda: ["task_update"])
    quiet_hours_enabled: bool = False
    quiet_hours_start_hour: int = 23
    quiet_hours_end_hour: int = 7
    suppress_low_priority_in_quiet_hours: bool = True


class Config(BaseSettings):
    """Root configuration for opencane."""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    lifelog: LifelogConfig = Field(default_factory=LifelogConfig)
    digital_task: DigitalTaskConfig = Field(default_factory=DigitalTaskConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from opencane.providers.registry import PROVIDERS
        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw_lower = kw.lower()
            return kw_lower in model_lower or kw_lower.replace("-", "_") in model_normalized

        # Explicit provider prefix in model name has highest priority.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name and p.api_key:
                return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords) and p.api_key:
                return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from opencane.providers.registry import find_by_name
        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # (like Moonshot) set their base URL via env vars in _setup_env
        # to avoid polluting the global litellm.api_base.
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(
        env_prefix="NANOBOT_",
        env_nested_delimiter="__"
    )
