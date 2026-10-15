"""Provider adapter contract tests. No API calls or credentials required."""
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from app.services.openai_planner_runtime import (
    execute_registered_call, openai_tool_specs, run_openai_planner,
)


class FakeTool:
    name = "read_only_test"

    def to_dict(self):
        return {
            "name": self.name,
            "description": "Read-only test",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }

    async def __call__(self, **kwargs):
        return json.dumps({"query": kwargs["query"], "verified": True})


class OpenAIPlannerAdapterTests(unittest.TestCase):
    def test_registry_schema_preserved(self):
        spec = openai_tool_specs([FakeTool()])[0]
        self.assertEqual(spec["function"]["name"], "read_only_test")
        self.assertEqual(spec["function"]["parameters"]["required"], ["query"])

    def test_tool_dispatch(self):
        result = asyncio.run(execute_registered_call(
            {"read_only_test": FakeTool()}, "read_only_test", '{"query":"WRN"}',
        ))
        self.assertEqual(json.loads(result)["query"], "WRN")

    def test_unknown_tool_fails_closed(self):
        result = asyncio.run(execute_registered_call({}, "invented", "{}"))
        self.assertEqual(json.loads(result)["error"], "unknown_tool")

    def test_non_object_arguments_rejected(self):
        result = asyncio.run(execute_registered_call(
            {"read_only_test": FakeTool()}, "read_only_test", "[]",
        ))
        self.assertEqual(json.loads(result)["error"], "invalid_arguments")

    def test_mocked_tool_execution_and_resume(self):
        def response(content, calls):
            return NS(choices=[NS(message=NS(
                content=content, tool_calls=calls,
                model_dump=lambda **kw: {"role": "assistant", "content": content,
                                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name,
                                         "arguments": c.function.arguments}} for c in calls]},
            ))])
        call = NS(id="tool-1", function=NS(name="read_only_test", arguments='{"query":"WRN"}'))
        client = NS(chat=NS(completions=NS(create=AsyncMock(
            side_effect=[response(None, [call]), response("Completed", [])]))))
        checkpoints = []
        async def save(history):
            checkpoints.append(list(history))
        with patch("app.services.openai_planner_runtime.AsyncOpenAI", return_value=client):
            receipt, history = asyncio.run(run_openai_planner(
                system="test", model="gpt-4.1-mini", messages=[{"role": "user", "content": "WRN"}],
                tools=[FakeTool()], on_step=save,
            ))
        self.assertEqual(receipt, "Completed")
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(history[-2]["role"], "tool")
        self.assertTrue(json.loads(history[-2]["content"])["verified"])
        resumed = NS(chat=NS(completions=NS(create=AsyncMock(
            return_value=response("Resumed", [])))))
        with patch("app.services.openai_planner_runtime.AsyncOpenAI", return_value=resumed):
            answer, _ = asyncio.run(run_openai_planner(
                system="test", model="gpt-4.1-mini", messages=checkpoints[0], tools=[FakeTool()],
            ))
        self.assertEqual(answer, "Resumed")
        sent = resumed.chat.completions.create.await_args.kwargs["messages"]
        self.assertEqual(sent[-2]["role"], "tool")
        self.assertEqual(sent[-1]["content"], "Resumed")

    def test_mocked_spend_limit_stops_before_tool(self):
        call = NS(id="tool-1", function=NS(name="read_only_test", arguments='{"query":"WRN"}'))
        reply = NS(choices=[NS(message=NS(content=None, tool_calls=[call]))])
        client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=reply))))
        async def over_budget(response):
            raise RuntimeError("spend ceiling exceeded")
        with patch("app.services.openai_planner_runtime.AsyncOpenAI", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "ceiling"):
                asyncio.run(run_openai_planner(
                    system="test", model="gpt-4.1-mini", messages=[{"role": "user", "content": "WRN"}],
                    tools=[FakeTool()], on_turn=over_budget,
                ))
        self.assertEqual(client.chat.completions.create.await_count, 1)


if __name__ == "__main__":
    unittest.main()
