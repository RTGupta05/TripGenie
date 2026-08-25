import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auth import AuthService, AuthenticationError
from chat import ChatApplication, ConversationNotFoundError


def main():
    parser = argparse.ArgumentParser(description="Personal Travel Planner")
    parser.add_argument("--conversation-id", help="Existing conversation UUID to continue.")
    args = parser.parse_args()

    auth = AuthService()

    print("=" * 50)
    print("       Personal Travel Planner")
    print("=" * 50)

    while True:
        print("\n1. Login")
        print("2. Register")
        print("3. Exit")

        choice = input("Choose an option: ").strip()

        if choice == "3":
            print("Goodbye!")
            return

        if choice == "2":
            print("\n--- Register ---")

            name = input("Name: ").strip()
            email = input("Email: ").strip()
            password = input("Password: ").strip()

            try:
                user_id = auth.create_user(
                    email=email,
                    password=password,
                    name=name,
                )

                print("\nRegistration successful.")
                print("Please login with your new account.")

            except ValueError as exc:
                print(f"Registration failed: {exc}")

            continue

        if choice == "1":
            print("\n--- Login ---")

            email = input("Email: ").strip()
            password = input("Password: ").strip()

            try:
                user = auth.authenticate(email, password)
                break

            except AuthenticationError as exc:
                print(f"Login failed: {exc}")

        else:
            print("Invalid option.")
            
    
    session_id = auth.create_session(user["id"])
    print(f"Logged in as {user['name']} ({user['email']})")
    print("Session created. The session ID is managed by the application.")

    try:
        # ChatApplication handles generating a new ID and setting up the DB automatically 
        # if args.conversation_id is None.
        chatbot = ChatApplication(
            user_id=user["id"],
            conversation_id=args.conversation_id,
        )
    except ConversationNotFoundError:
        print("Conversation not found or you are not authorized to access it.")
        auth.revoke_session(session_id)
        return

    print(f"Conversation: {chatbot.get_conversation_id()}")
    print("Type 'exit' to quit.")
    print("Type 'clear' to reset this conversation.")
    print("Type 'conversations' to list your conversations.")
    print()

    try:
        while True:
            question = input("You: ").strip()
            if not question:
                continue

            if question.lower() in {"exit", "quit"}:
                print("Goodbye!")
                break

            if question.lower() == "clear":
                chatbot.clear_history()
                print("Conversation cleared.\n")
                continue

            if question.lower() == "conversations":
                for item in ChatApplication.list_conversations(user["id"]):
                    print(f"{item['id']} | {item['title']}")
                print()
                continue

            try:
                answer = chatbot.chat(question)
                print(f"AI: {answer}\n")
            except Exception as exc:
                print(f"Error: {exc}\n")
    finally:
        auth.revoke_session(session_id)


if __name__ == "__main__":
    main()
