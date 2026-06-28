import pytest
from civ_mcp.arena.backends import OpenAICompatBackend, Reply

class _FakeUsage:  prompt_tokens = 11; completion_tokens = 3
class _FakeMsg:    content = "ok"; tool_calls = None
class _FakeChoice: message = _FakeMsg()
class _FakeResp:   choices = [_FakeChoice()]; usage = _FakeUsage()

@pytest.mark.asyncio
async def test_chat_parses_reply(monkeypatch):
    b = OpenAICompatBackend("http://x/v1", "k", "m")
    async def fake_create(**kw): return _FakeResp()
    monkeypatch.setattr(b._client.chat.completions, "create", fake_create)
    r = await b.chat([{"role": "user", "content": "hi"}], tools=[])
    assert isinstance(r, Reply)
    assert r.text == "ok" and r.prompt_tokens == 11 and r.completion_tokens == 3
