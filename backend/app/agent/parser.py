"""Parse LLM ReAct output into Thought / Action / Answer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ParsedOutput:
    type: Literal["thought", "action", "answer"]
    content: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)


def parse_agent_output(text: str) -> ParsedOutput:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    if text.startswith("Answer:"):
        json_str = text[len("Answer:") :].strip()
        json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
        json_str = re.sub(r"\s*```$", "", json_str)
        return ParsedOutput(type="answer", content=json_str)

    if text.startswith("Action:"):
        action_str = text[len("Action:") :].strip()
        match = re.match(r"(\w+)\((.+)\)", action_str, re.DOTALL)
        if match:
            tool_name = match.group(1)
            args_str = match.group(2).strip()
            try:
                tool_args = (
                    json.loads("{" + args_str + "}")
                    if not args_str.startswith("{")
                    else json.loads(args_str)
                )
            except json.JSONDecodeError:
                tool_args = _parse_kwargs(args_str)
            return ParsedOutput(type="action", tool_name=tool_name, tool_args=tool_args)

    if text.startswith("Thought:"):
        return ParsedOutput(type="thought", content=text[len("Thought:") :].strip())

    if text.startswith("{"):
        try:
            json.loads(text)
            return ParsedOutput(type="answer", content=text)
        except json.JSONDecodeError:
            pass

    return ParsedOutput(type="thought", content=text)


def _parse_kwargs(s: str) -> dict:
    result = {}
    for pair in s.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            k = k.strip().strip("\"'")
            v = v.strip().strip("\"'")
            try:
                v = int(v)
            except ValueError:
                pass
            result[k] = v
    return result
