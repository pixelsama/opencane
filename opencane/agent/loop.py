"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import json_repair
from loguru import logger

from opencane.agent.context import ContextBuilder
from opencane.agent.memory import UnifiedMemoryProvider
from opencane.agent.subagent import SubagentManager
from opencane.agent.tools.cron import CronTool
from opencane.agent.tools.domain_manager import ToolDomainManager
from opencane.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from opencane.agent.tools.message import MessageTool
from opencane.agent.tools.registry import ToolRegistry
from opencane.agent.tools.shell import ExecTool
from opencane.agent.tools.spawn import SpawnTool
from opencane.agent.tools.web import WebFetchTool, WebSearchTool
from opencane.bus.events import InboundMessage, OutboundMessage
from opencane.bus.queue import MessageBus
from opencane.config.schema import ExecToolConfig
from opencane.providers.base import LLMProvider
from opencane.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from opencane.cron.service import CronService


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _shorten(value: str, max_chars: int = 300) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        safety_policy: Any | None = None,
        lifelog_service: Any | None = None,
        max_subagents: int = 4,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.safety_policy = safety_policy
        self.lifelog = lifelog_service

        self.memory = UnifiedMemoryProvider(
            workspace,
            lifelog_service=lifelog_service,
        )
        self.context = ContextBuilder(
            workspace,
            memory_provider=self.memory,
        )
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.tool_domains = ToolDomainManager()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            max_running_tasks=max_subagents,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._register_default_tools()

    def _should_apply_safety(
        self,
        *,
        channel: str,
        allowed_tool_names: set[str] | None,
        require_tool_use: bool,
    ) -> bool:
        if self.safety_policy is None or not hasattr(self.safety_policy, "evaluate"):
            return False
        if channel == "hardware":
            # Hardware runtime has dedicated safety policy with lifelog auditing.
            return False
        if require_tool_use:
            return False
        if allowed_tool_names is not None:
            return False
        return bool(getattr(self.safety_policy, "enabled", True))

    def _apply_safety_if_needed(
        self,
        *,
        content: str,
        source: str,
        channel: str,
        session_id: str | None,
        allowed_tool_names: set[str] | None,
        require_tool_use: bool,
    ) -> str:
        text = str(content or "").strip()
        if not text:
            return text
        # Keep control tokens intact for policy-driven execution paths.
        if text in {"NO_TOOL_USED", "MCP_FALLBACK_REQUIRED"}:
            return text
        if not self._should_apply_safety(
            channel=channel,
            allowed_tool_names=allowed_tool_names,
            require_tool_use=require_tool_use,
        ):
            return text
        try:
            context = {"channel": channel}
            if session_id:
                context["session_id"] = session_id
            raw = self.safety_policy.evaluate(
                text=text,
                source=source,
                confidence=0.75,
                risk_level="P3",
                context=context,
            )
            if hasattr(raw, "to_dict"):
                decision = raw.to_dict()
            elif isinstance(raw, dict):
                decision = dict(raw)
            else:
                return text
            safe_text = str(decision.get("text") or text).strip() or text
            if safe_text != text:
                logger.debug(
                    f"agent safety applied source={source} channel={channel} "
                    f"reason={decision.get('reason')} flags={decision.get('flags')}"
                )
            self._record_agent_safety_audit(
                source=source,
                channel=channel,
                session_id=session_id,
                input_text=text,
                decision=decision,
                confidence=0.75,
                risk_level="P3",
                context=context,
            )
            return safe_text
        except Exception as e:
            logger.debug(f"agent safety evaluate failed: {e}")
            return text

    def _record_agent_safety_audit(
        self,
        *,
        source: str,
        channel: str,
        session_id: str | None,
        input_text: str,
        decision: dict[str, Any],
        confidence: float,
        risk_level: str,
        context: dict[str, Any],
    ) -> None:
        if self.lifelog is None or not hasattr(self.lifelog, "record_runtime_event"):
            return
        sid = str(session_id or f"{channel}:unknown")
        output_text = str(decision.get("text") or input_text).strip() or input_text
        output_risk = str(decision.get("risk_level") or risk_level or "P3")
        output_conf = _to_float(decision.get("confidence"), default=confidence)
        flags = decision.get("flags")
        if not isinstance(flags, list):
            flags = []
        rule_ids = decision.get("rule_ids")
        if not isinstance(rule_ids, list):
            rule_ids = []
        evidence = decision.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        payload = {
            "trace_id": f"agent-safety-{uuid.uuid4().hex[:12]}",
            "source": source,
            "channel": channel,
            "reason": str(decision.get("reason") or ""),
            "flags": [str(flag) for flag in flags],
            "policy_version": str(decision.get("policy_version") or "unknown"),
            "rule_ids": [str(rule_id) for rule_id in rule_ids],
            "evidence": evidence,
            "input_text": _shorten(input_text, 300),
            "output_text": _shorten(output_text, 300),
            "input_risk_level": str(risk_level),
            "output_risk_level": output_risk,
            "downgraded": bool(decision.get("downgraded")),
            "context": dict(context or {}),
        }
        try:
            self.lifelog.record_runtime_event(
                session_id=sid,
                event_type="safety_policy",
                payload=payload,
                risk_level=output_risk,
                confidence=output_conf,
            )
        except Exception as e:
            logger.debug(f"agent safety audit failed: {e}")

    def _filter_message_tool_content(self, content: str, channel: str, chat_id: str) -> str:
        return self._apply_safety_if_needed(
            content=content,
            source="message_tool",
            channel=channel,
            session_id=f"{channel}:{chat_id}",
            allowed_tool_names=None,
            require_tool_use=False,
        )

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tool_domains.register_tool(
            "read_file",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tool_domains.register_tool(
            "write_file",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tool_domains.register_tool(
            "edit_file",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        self.tool_domains.register_tool(
            "list_dir",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )

        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        self.tool_domains.register_tool(
            "exec",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )

        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tool_domains.register_tool(
            "web_search",
            domain="server_tools",
            allowed_channels={"cli", "hardware"},
            allow_system=False,
        )
        self.tools.register(WebFetchTool())
        self.tool_domains.register_tool(
            "web_fetch",
            domain="server_tools",
            allowed_channels={"cli", "hardware"},
            allow_system=False,
        )

        # Message tool
        message_tool = MessageTool(
            send_callback=self.bus.publish_outbound,
            pre_send_filter=self._filter_message_tool_content,
        )
        self.tools.register(message_tool)
        self.tool_domains.register_tool(
            "message",
            domain="device_tools",
            allowed_channels={"cli"},
            allow_system=False,
        )

        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        self.tool_domains.register_tool(
            "spawn",
            domain="server_tools",
            allowed_channels={"cli"},
            allow_system=False,
            max_calls_per_turn=1,
        )

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
            self.tool_domains.register_tool(
                "cron",
                domain="server_tools",
                allowed_channels={"cli"},
                allow_system=False,
            )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from opencane.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
            self.tool_domains.register_mcp_tools(self.tools.tool_names)
        except Exception as e:
            logger.error(f"Failed to connect MCP servers (will retry next message): {e}")
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

    def _schedule_consolidation(self, session: Session, *, archive_all: bool = False) -> None:
        """Start memory consolidation for a session, deduplicated by session key."""
        if session.key in self._consolidating:
            return
        self._consolidating.add(session.key)

        async def _run() -> None:
            try:
                await self._consolidate_memory(session, archive_all=archive_all)
            finally:
                self._consolidating.discard(session.key)

        asyncio.create_task(_run())

    async def _build_prompt_memory_context(
        self,
        *,
        query: str,
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> str:
        """Build prompt memory context from file memory and optional retrieval memory."""
        file_memory = self.memory.get_file_memory_context()
        retrieval_memory = await self.memory.retrieve_context(
            query=query,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
        )
        return self.memory.build_prompt_memory_context(
            file_memory_context=file_memory,
            retrieval_context=retrieval_memory,
        )

    def _build_runtime_context_block(self, metadata: dict[str, Any] | None) -> str:
        info = metadata if isinstance(metadata, dict) else {}
        raw = info.get("runtime_context")
        context = raw if isinstance(raw, dict) else {}
        if not context:
            return ""
        lines = ["## Device Runtime Context"]
        device_id = str(context.get("device_id") or "").strip()
        session_id = str(context.get("session_id") or "").strip()
        state = str(context.get("state") or "").strip()
        trace_id = str(context.get("trace_id") or "").strip()
        transcript = str(context.get("transcript") or "").strip()
        if device_id:
            lines.append(f"- device_id: {device_id}")
        if session_id:
            lines.append(f"- session_id: {session_id}")
        if state:
            lines.append(f"- connection_state: {state}")
        if trace_id:
            lines.append(f"- trace_id: {trace_id}")
        if transcript:
            lines.append(f"- transcript: {_shorten(transcript, 200)}")

        for key in ("telemetry", "session_metadata", "tool_policy"):
            value = context.get(key)
            if not isinstance(value, dict) or not value:
                continue
            try:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except Exception:
                continue
            lines.append(f"- {key}: {_shorten(text, 320)}")
        return "\n".join(lines)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        *,
        channel: str = "cli",
        is_system: bool = False,
        allowed_tool_names: set[str] | None = None,
        blocked_tool_names: set[str] | None = None,
        require_tool_use: bool = False,
    ) -> tuple[str | None, list[str]]:
        """
        Run the agent iteration loop.

        Args:
            initial_messages: Starting messages for the LLM conversation.

        Returns:
            Tuple of (final_content, list_of_tools_used).
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        tool_call_counts: dict[str, int] = {}

        while iteration < self.max_iterations:
            iteration += 1

            effective_allowed = self.tool_domains.allowed_tool_names(
                self.tools.tool_names,
                channel=channel,
                is_system=is_system,
                explicit_allowlist=allowed_tool_names,
            )
            if blocked_tool_names:
                blocked = {str(name).strip() for name in blocked_tool_names if str(name).strip()}
                effective_allowed = {name for name in effective_allowed if name not in blocked}
            tool_defs = self.tools.get_definitions()
            if effective_allowed:
                tool_defs = [
                    item
                    for item in tool_defs
                    if str(item.get("function", {}).get("name", "")) in effective_allowed
                ]
            else:
                tool_defs = []

            response = await self.provider.chat(
                messages=messages,
                tools=tool_defs,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    if tool_call.name not in effective_allowed:
                        result = (
                            f"Error: Tool '{tool_call.name}' is not allowed in current routing policy"
                        )
                    else:
                        can_execute, deny_reason = self.tool_domains.can_execute(
                            tool_call.name,
                            channel=channel,
                            is_system=is_system,
                            call_counts=tool_call_counts,
                            enforce_channel_policy=True,
                        )
                        if not can_execute:
                            result = (
                                f"Error: Tool '{tool_call.name}' is blocked by execution guard ({deny_reason})"
                            )
                        else:
                            result = await self.tools.execute(tool_call.name, tool_call.arguments)
                            tool_call_counts[tool_call.name] = int(tool_call_counts.get(tool_call.name, 0)) + 1
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            else:
                final_content = response.content
                if require_tool_use and not tools_used:
                    final_content = "NO_TOOL_USED"
                break

        return final_content, tools_used

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        *,
        allowed_tool_names: set[str] | None = None,
        blocked_tool_names: set[str] | None = None,
        require_tool_use: bool = False,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).

        Returns:
            The response message, or None if no response needed.
        """
        # System messages route back via chat_id ("channel:chat_id")
        if msg.channel == "system":
            return await self._process_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Handle slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            # Capture messages before clearing (avoid race condition with background task)
            messages_to_archive = session.messages.copy()
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            temp_session = Session(key=session.key)
            temp_session.messages = messages_to_archive
            self._schedule_consolidation(temp_session, archive_all=True)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started. Memory consolidation in progress.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🦯 OpenCane commands:\n/new — Start a new conversation\n/help — Show available commands")

        if len(session.messages) > self.memory_window:
            self._schedule_consolidation(session)

        self._set_tool_context(msg.channel, msg.chat_id)
        memory_context = await self._build_prompt_memory_context(
            query=msg.content,
            session_key=key,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        runtime_context_block = self._build_runtime_context_block(msg.metadata)
        if runtime_context_block:
            parts = [segment for segment in [memory_context.strip(), runtime_context_block] if segment]
            memory_context = "\n\n".join(parts).strip()
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            memory_context_override=memory_context,
        )
        final_content, tools_used = await self._run_agent_loop(
            initial_messages,
            channel=msg.channel,
            is_system=False,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            require_tool_use=require_tool_use,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        final_content = self._apply_safety_if_needed(
            content=final_content,
            source="agent_reply",
            channel=msg.channel,
            session_id=session.key,
            allowed_tool_names=allowed_tool_names,
            require_tool_use=require_tool_use,
        )

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        session.add_message("user", msg.content)
        session.add_message("assistant", final_content,
                            tools_used=tools_used if tools_used else None)
        try:
            self.memory.record_turn(
                session_key=session.key,
                channel=msg.channel,
                chat_id=msg.chat_id,
                user_text=msg.content,
                assistant_text=final_content,
                tools_used=tools_used,
            )
        except Exception as e:
            logger.debug(f"layered memory record_turn failed: {e}")
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        self._set_tool_context(origin_channel, origin_chat_id)
        memory_context = await self._build_prompt_memory_context(
            query=msg.content,
            session_key=session_key,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
            memory_context_override=memory_context,
        )
        final_content, _ = await self._run_agent_loop(
            initial_messages,
            channel=origin_channel,
            is_system=True,
        )

        if final_content is None:
            final_content = "Background task completed."
        final_content = self._apply_safety_if_needed(
            content=final_content,
            source="system_reply",
            channel=origin_channel,
            session_id=session.key,
            allowed_tool_names=None,
            require_tool_use=False,
        )

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        try:
            self.memory.record_turn(
                session_key=session.key,
                channel=origin_channel,
                chat_id=origin_chat_id,
                user_text=f"[System: {msg.sender_id}] {msg.content}",
                assistant_text=final_content,
                tools_used=[],
            )
        except Exception as e:
            logger.debug(f"layered memory record_turn failed: {e}")
        self.sessions.save(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )

    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md.

        Args:
            archive_all: If True, clear all messages and reset session (for /new command).
                       If False, only write to files without modifying session.
        """
        memory = self.memory

        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info(f"Memory consolidation (archive_all): {len(session.messages)} total messages archived")
        else:
            keep_count = self.memory_window // 2
            if len(session.messages) <= keep_count:
                logger.debug(f"Session {session.key}: No consolidation needed (messages={len(session.messages)}, keep={keep_count})")
                return

            messages_to_process = len(session.messages) - session.last_consolidated
            if messages_to_process <= 0:
                logger.debug(f"Session {session.key}: No new messages to consolidate (last_consolidated={session.last_consolidated}, total={len(session.messages)})")
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return
            logger.info(f"Memory consolidation started: {len(session.messages)} total, {len(old_messages)} new to consolidate, {keep_count} keep")

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")
        conversation = "\n".join(lines)
        current_memory = memory.read_long_term()

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            text = (response.content or "").strip()
            if not text:
                logger.warning("Memory consolidation: LLM returned empty response, skipping")
                return
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text)
            if not isinstance(result, dict):
                logger.warning(f"Memory consolidation: unexpected response type, skipping. Response: {text[:200]}")
                return

            if entry := result.get("history_entry"):
                memory.append_history(entry)
            if update := result.get("memory_update"):
                if update != current_memory:
                    memory.write_long_term(update)

            if archive_all:
                session.last_consolidated = 0
            else:
                session.last_consolidated = len(session.messages) - keep_count
            logger.info(f"Memory consolidation done: {len(session.messages)} messages, last_consolidated={session.last_consolidated}")
        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        allowed_tool_names: set[str] | None = None,
        blocked_tool_names: set[str] | None = None,
        require_tool_use: bool = False,
        message_metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
            blocked_tool_names: Optional deny-list for tool execution.
            message_metadata: Optional per-message metadata for context injection.

        Returns:
            The agent's response.
        """
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=dict(message_metadata or {}),
        )

        response = await self._process_message(
            msg,
            session_key=session_key,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            require_tool_use=require_tool_use,
        )
        return response.content if response else ""

    async def list_registered_tools(self, *, connect_mcp: bool = True) -> list[str]:
        """List current registered tool names, optionally connecting MCP first."""
        if connect_mcp:
            await self._connect_mcp()
        return self.tools.tool_names
