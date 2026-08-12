from .chat import ChatApplication


def main():

    chatbot = ChatApplication()

    print("=" * 40)
    print("       This is you personal Travel Planner")
    print("=" * 40)
    print("Type 'exit' to quit.")
    print("Type 'clear' to reset conversation.\n")

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