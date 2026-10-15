"""Provider adapter contract tests. No API calls or credentials required."""
import asyncio
import json
import unittest

from app.services.openai_planner_runtime import (
    execute_registered_call, openai_tool_specs,
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


if __name__ == "__main__":
    unittest.main()
