import json

import httpx
import pytest

from app.providers.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_chat_stream_is_converted_to_events():
    requests = []
    def handler(request):
        assert request.url.path == "/api/chat"
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, text='{"message":{"content":"Bon"},"done":false}\n{"message":{"content":"jour"},"done":true}\n')
    provider = OllamaProvider("http://test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    events = [event async for event in provider.run({"name": "test", "context_tokens": 128}, [{"role": "user", "content": "x"}])]
    assert [event.type for event in events] == ["token", "token", "completed"]
    assert json.loads(requests[0].content)["options"]["num_ctx"] == 128
    await provider.client.aclose()
