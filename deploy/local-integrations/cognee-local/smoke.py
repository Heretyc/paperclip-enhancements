"""Exercise the running MCP server, never open a second embedded-store writer.

Run with: docker compose exec -T cognee python - < deploy/local-integrations/cognee-local/smoke.py
Pass `recall` as the last argument to verify persistence without another write.
Only the dedicated synthetic installation-test dataset is used.
"""
import asyncio
import json
import sys
import os

from fastmcp import Client

DATASET = os.getenv("COGNEE_TEST_DATASET", "paperclip_install_smoke")
TEXT = (
    "This is synthetic installation test data. "
    "Marie Curie was born in Warsaw and worked at the University of Paris. "
    "She won the Nobel Prize in Physics in 1903 with Pierre Curie and Henri Becquerel."
)


async def main():
    async with Client(os.getenv("COGNEE_MCP_URL", "http://127.0.0.1:8000/mcp"), timeout=900) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert {"remember", "recall", "forget", "cognify_status"} <= names
        print(json.dumps({"tools": sorted(names)}), flush=True)
        if len(sys.argv) == 1:
            remembered = await client.call_tool("remember", {
                "data": TEXT, "dataset_name": DATASET,
                "self_improvement": False,
            })
            print(json.dumps({"remember": [c.model_dump(mode="json") for c in remembered.content]}), flush=True)
            assert not remembered.is_error
            assert not any(getattr(c, "text", "").startswith("Error:") for c in remembered.content)
        for search_type in ("CHUNKS", None):
            arguments = {"query": "Where was Marie Curie born?", "datasets": DATASET, "top_k": 3}
            if search_type:
                arguments["search_type"] = search_type
            recalled = await client.call_tool("recall", arguments)
            body = "\n".join(getattr(c, "text", "") for c in recalled.content)
            print(json.dumps({"search_type": search_type or "default", "recall": body}), flush=True)
            assert not recalled.is_error
            assert "Warsaw" in body, body
            assert not body.startswith("Error:"), body
        print(json.dumps({"ok": True, "dataset": DATASET}), flush=True)


asyncio.run(main())
