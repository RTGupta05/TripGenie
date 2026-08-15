from langchain_ollama import ChatOllama

from .config import (
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
    TEMPERATURE,
    OLLAMA_CONTEXT_WINDOW,
    OLLAMA_MAX_OUTPUT_TOKENS,
)


def create_llm():

    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=TEMPERATURE,
        num_ctx=OLLAMA_CONTEXT_WINDOW,
        num_predict=OLLAMA_MAX_OUTPUT_TOKENS,
    )