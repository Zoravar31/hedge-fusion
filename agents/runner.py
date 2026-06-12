"""
Agent Runner
============
Runs any single agent using OpenAI chat completions with function calling.
Handles multi-round tool use automatically.

All 9 agents in HedgeFusion use this same runner — only their
system prompt and allowed tools differ.
"""

import json
import os
from typing import Any

from loguru import logger
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = os.getenv("MODEL_NAME", "gpt-4o-mini")


def run_agent(
    agent_name: str,
    system_prompt: str,
    user_message: str,
    tools: list | None = None,
    tool_map: dict | None = None,
    max_tool_rounds: int = 6,
    temperature: float = 0.2,
) -> str:
    """
    Run a single agent with optional OpenAI function calling.

    Parameters
    ----------
    agent_name   : Label for logging.
    system_prompt: The agent's role and instructions.
    user_message : The task or context passed to this agent.
    tools        : OpenAI tool definitions (function schemas).
    tool_map     : Dict mapping function name -> callable.
    max_tool_rounds: Max rounds of tool use before forcing a final answer.
    temperature  : Sampling temperature (lower = more deterministic).

    Returns
    -------
    str: Agent's final text response.
    """
    logger.info("▶ {} starting", agent_name)
    messages = [
        {"role": "system",  "content": system_prompt},
        {"role": "user",    "content": user_message},
    ]

    for round_num in range(max_tool_rounds + 1):
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        if not msg.tool_calls:
            result = msg.content or ""
            logger.info("◀ {} done ({} chars)", agent_name, len(result))
            return result

        messages.append(msg)
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            logger.info("  [tool] {}({})", fn_name, list(fn_args.keys()))
            if tool_map and fn_name in tool_map:
                try:
                    result = tool_map[fn_name](**fn_args)
                except Exception as e:
                    result = json.dumps({"error": str(e)})
            else:
                result = json.dumps({"error": f"Unknown tool: {fn_name}"})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # Force final answer after max rounds
    final = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
    )
    return final.choices[0].message.content or ""


def parse_json_response(raw: str) -> dict:
    """
    Extract a JSON dict from an agent response.
    Handles markdown code fences and partial responses gracefully.
    """
    import re
    if not raw:
        return {}
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {"raw_response": raw[:500]}
