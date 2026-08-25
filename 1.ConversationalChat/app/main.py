import argparse

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from .chat import ChatApplication


def main():

    parser = argparse.ArgumentParser(
        description="Personal Travel Planner"
    )

    parser.add_argument(
        "--conversation-id",
        help=(
            "Existing conversation UUID to continue. "
            "If omitted, a new conversation is created."
        ),
    )

    args = parser.parse_args()

    chatbot = ChatApplication(
        conversation_id=args.conversation_id
    )

    print("=" * 50)
    print("       Your Personal Travel Planner")
    print("=" * 50)
    print(
        f"Conversation ID: {chatbot.get_conversation_id()}"
    )
    print()
    print("Type 'exit' to quit.")
    print("Type 'clear' to reset this conversation.")
    print()

    while True:

        try:
            question = input("You: ").strip()

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not question:
            continue

        if question.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        if question.lower() == "clear":

            chatbot.clear_history()

            print("Conversation cleared.\n")

            continue

        try:

            answer = chatbot.chat(question)

            print(f"AI: {answer}\n")

        except Exception as exc:

            print(f"Error: {exc}\n")


if __name__ == "__main__":
    main()