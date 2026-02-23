from __future__ import annotations

import asyncio

import pytest

from opencane.agent.tools.shell import ExecTool


class _FakeProcess:
    def __init__(self) -> None:
        self.killed = False
        self.wait_called = False
        self.returncode = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(999)
        return b"", b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_called = True
        self.returncode = -9
        return self.returncode


@pytest.mark.asyncio
async def test_exec_tool_waits_after_kill_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_process = _FakeProcess()
    tool = ExecTool(timeout=1)

    async def _fake_create_subprocess_shell(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return fake_process

    wait_for_calls = 0

    async def _fake_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
        del timeout
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr("opencane.agent.tools.shell.asyncio.create_subprocess_shell", _fake_create_subprocess_shell)
    monkeypatch.setattr("opencane.agent.tools.shell.asyncio.wait_for", _fake_wait_for)

    result = await tool.execute("sleep 60")
    assert "timed out" in result
    assert fake_process.killed is True
    assert fake_process.wait_called is True
    assert wait_for_calls == 2

