"""Phase 2 smoke test: verify end-to-end Python-to-Ollama integration.

Sends a single prompt to the local Ollama server through the official Python
client and prints the response. Run only after activating the project venv.
"""

from ollama import chat, ChatResponse

MODEL_NAME = "qwen2.5:7b-instruct"


def main() -> None:
    """Send one prompt to the model and print the response."""
    response: ChatResponse = chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": "Say the word READY and nothing else.",
            }
        ],
    )
    print(response.message.content)


if __name__ == "__main__":
    main()
