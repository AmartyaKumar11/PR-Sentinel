from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    fn: Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get_schemas_for_prompt(self) -> str:
        lines = []
        for t in self._tools.values():
            params = ", ".join(f"{k}: {v}" for k, v in t.parameters.items())
            lines.append(f"- {t.name}({params}): {t.description}")
        return "\n".join(lines)

    async def execute(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return await self._tools[name].fn(**kwargs)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())
