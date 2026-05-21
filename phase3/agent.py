"""SONiC intent-based agent: single-turn read-only mode.

Takes a natural-language question about the SONiC switch and answers it
by calling the read-only tools defined in tools.py. This is Phase 3:
no configuration changes, no multi-step planning, just question to answer.

Usage:
    python3 agent.py "what IP is configured on Ethernet0?"

The model name can be overridden with the AGENT_MODEL environment variable.
Default is qwen2.5:7b-instruct.
"""

import argparse
import logging
import os
import sys

from ollama import chat

import tools

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_TOOL_ROUND_TRIPS = 1

SYSTEM_PROMPT = (
    "You are an agent that answers questions about a SONiC network switch. "
    "Use the provided tools to query live state on the switch. "
    "Do not make up data. If a tool returns an error or says the data is "
    "not available, tell the user honestly rather than guessing. "
    "Keep your answers short and direct."
)

AVAILABLE_TOOLS = [
    tools.get_interface_ip,
    tools.list_configured_interfaces,
    tools.get_bgp_status,
]


def _execute_tool_call(tool_call) -> str:
    """Look up the tool by name and call it with the provided arguments.

    Args:
        tool_call: an Ollama ToolCall object with .function.name and
            .function.arguments.

    Returns:
        The string returned by the tool function. Always a string, since
        every tool in tools.py is defined to return a string.
    """
    tool_name = tool_call.function.name
    tool_args = dict(tool_call.function.arguments)
    logger.info("tool call: %s(%s)", tool_name, tool_args)

    tool_function = None
    for candidate in AVAILABLE_TOOLS:
        if candidate.__name__ == tool_name:
            tool_function = candidate
            break

    if tool_function is None:
        result = f"error: unknown tool {tool_name!r}"
        logger.warning(result)
        return result

    try:
        result = tool_function(**tool_args)
    except TypeError as exc:
        result = f"error: bad arguments to {tool_name}: {exc}"
        logger.warning(result)
        return result

    logger.info("tool result: %s", result)
    return result


def answer_question(question: str, model: str) -> str:
    """Run the agent loop and return the final answer.

    Args:
        question: the natural-language question from the user.
        model: the Ollama model name to use, e.g. "qwen2.5:7b-instruct".

    Returns:
        The agent's final answer as a string.

    Raises:
        RuntimeError: if the LLM call itself fails (e.g. Ollama unreachable).
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    logger.info("question: %s", question)

    try:
        response = chat(model=model, messages=messages, tools=AVAILABLE_TOOLS)
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc

    messages.append(response.message)

    round_trips = 0
    while response.message.tool_calls and round_trips < MAX_TOOL_ROUND_TRIPS:
        round_trips += 1
        for tool_call in response.message.tool_calls:
            result = _execute_tool_call(tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_call.function.name,
                    "content": result,
                }
            )

        try:
            response = chat(
                model=model, messages=messages, tools=AVAILABLE_TOOLS
            )
        except Exception as exc:
            raise RuntimeError(
                f"LLM follow-up call failed after tool execution: {exc}"
            ) from exc
        messages.append(response.message)

    final_content = response.message.content or ""
    logger.info("final answer: %s", final_content)
    return final_content


def main() -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        description="Query the SONiC switch with a natural-language question."
    )
    parser.add_argument(
        "question",
        help="the question to ask, in quotes",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show tool calls and intermediate steps on stderr",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENT_MODEL", DEFAULT_MODEL),
        help=f"Ollama model name (default: {DEFAULT_MODEL}, "
        f"or AGENT_MODEL env var)",
    )
    args = parser.parse_args()

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        answer = answer_question(args.question, args.model)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
