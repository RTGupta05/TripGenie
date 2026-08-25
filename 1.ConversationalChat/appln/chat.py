from __future__ import annotations
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from uuid import UUID, uuid4


from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from config import (
    DATABASE_URL,
    RECENT_MESSAGE_COUNT,
    SUMMARY_TRIGGER_MESSAGES,
    SUMMARY_MAX_CHARS,
    MAX_PROMPT_CHARS,
)
from llm import create_llm
from auth import AuthService


SYSTEM_PROMPT = (
    "You are an expert detail oriented global travel planner and local insider. "
    "Give crisp and concise answers."
)


class ConversationNotFoundError(Exception):
    """Raised when a conversation does not exist or is not owned by the user."""


class ChatApplication:
    """Conversation and LLM service scoped to one authenticated user."""

    def __init__(self, user_id: str, conversation_id: str | None = None):
        if not user_id:
            raise ValueError("user_id is required.")

        self.llm = create_llm()
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.user_id = UUID(user_id)
        self.conversation_id = UUID(conversation_id) if conversation_id else uuid4()
        self._initialize_database()
        self._ensure_conversation()

    def _initialize_database(self) -> None:
        # Ensure the authentication tables exist before conversations reference users.
        AuthService(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY,
                    user_id UUID NULL,
                    title VARCHAR(500) NOT NULL DEFAULT 'New Conversation',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role VARCHAR(20) NOT NULL CHECK (role IN ('human', 'ai')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
                    ON conversations(user_id, updated_at DESC);
            """))

            # Safe migration for the existing database created by the previous version.
            conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id UUID NULL"
            ))
            conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title VARCHAR(500) NOT NULL DEFAULT 'New Conversation'"
            ))
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'conversations_user_fk'
                    ) THEN
                        ALTER TABLE conversations
                        ADD CONSTRAINT conversations_user_fk
                        FOREIGN KEY (user_id) REFERENCES users(id)
                        ON DELETE CASCADE
                        NOT VALID;
                    END IF;
                END $$;
            """))

    def _ensure_conversation(self) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id, user_id FROM conversations WHERE id = :id"),
                {"id": self.conversation_id},
            ).mappings().first()

            if existing:
                if existing["user_id"] != self.user_id:
                    raise ConversationNotFoundError("Conversation not found.")
                return

            conn.execute(
                text("""
                    INSERT INTO conversations (id, user_id, title)
                    VALUES (:id, :user_id, 'New Conversation')
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            )

    def get_conversation_id(self) -> str:
        return str(self.conversation_id)

    def get_conversation(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT id, title, summary, created_at, updated_at
                    FROM conversations
                    WHERE id = :id AND user_id = :user_id
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            ).mappings().first()

        if not row:
            raise ConversationNotFoundError("Conversation not found.")
        return dict(row)

    def _get_summary(self) -> str:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT summary FROM conversations
                    WHERE id = :id AND user_id = :user_id
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            ).mappings().first()
            return row["summary"] if row and row["summary"] else ""

    def _save_summary(self, summary: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE conversations
                    SET summary = :summary, updated_at = NOW()
                    WHERE id = :id AND user_id = :user_id
                """),
                {
                    "id": self.conversation_id,
                    "user_id": self.user_id,
                    "summary": summary,
                },
            )

    def _get_recent_messages(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT role, content
                    FROM messages
                    WHERE conversation_id = :id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                """),
                {"id": self.conversation_id, "limit": RECENT_MESSAGE_COUNT},
            ).mappings().all()

        return [
            HumanMessage(content=r["content"])
            if r["role"] == "human"
            else AIMessage(content=r["content"])
            for r in reversed(rows)
        ]

    def _save_exchange(self, question: str, answer: str) -> None:
        with self.engine.begin() as conn:
            # The ownership predicate prevents writes to another user's conversation.
            result = conn.execute(
                text("""
                    INSERT INTO messages (conversation_id, role, content)
                    SELECT :id, 'human', :q
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE id = :id AND user_id = :user_id
                    )
                """),
                {"id": self.conversation_id, "user_id": self.user_id, "q": question},
            )
            if result.rowcount != 1:
                raise ConversationNotFoundError("Conversation not found.")

            conn.execute(
                text("""
                    INSERT INTO messages (conversation_id, role, content)
                    VALUES (:id, 'ai', :a)
                """),
                {"id": self.conversation_id, "a": answer},
            )
            conn.execute(
                text("""
                    UPDATE conversations
                    SET updated_at = NOW()
                    WHERE id = :id AND user_id = :user_id
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            )

    def _get_message_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.conversation_id = :id AND c.user_id = :user_id
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            ).scalar_one()

    def _trim_recent_messages(self, messages):
        if not messages:
            return []

        budget = MAX_PROMPT_CHARS // 2
        selected, chars = [], 0

        for msg in reversed(messages):
            msg_len = len(str(msg.content))
            if selected and chars + msg_len > budget:
                break
            selected.append(msg)
            chars += msg_len

        return selected[::-1]

    def _build_context(self):
        return self._get_summary(), self._trim_recent_messages(self._get_recent_messages())

    def _get_messages_for_summary(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT m.id, m.role, m.content
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.conversation_id = :id AND c.user_id = :user_id
                    ORDER BY m.created_at ASC, m.id ASC
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            ).mappings().all()

        return rows[:-RECENT_MESSAGE_COUNT] if len(rows) > RECENT_MESSAGE_COUNT else []

    def _create_summary(self) -> None:
        if not (old_messages := self._get_messages_for_summary()):
            return

        parts = [
            f"{'User' if m['role'] == 'human' else 'Assistant'}: {m['content']}"
            for m in old_messages
        ]
        old_conv = "\n\n".join(parts)[-16000:]

        prompt = [
            SystemMessage(content=(
                "You maintain long-term memory for a travel planning chatbot.\n"
                "Create a compact, factual memory of the conversation.\n"
                "Preserve important information such as: destinations, travel dates, number of travelers, "
                "budgets, user preferences, constraints, decisions already made, important questions and "
                "answers, and unresolved requests.\n"
                "Remove greetings and conversational filler. Do not invent facts.\n"
                f"Keep the memory below {SUMMARY_MAX_CHARS} characters."
            )),
            HumanMessage(content=f"Existing memory:\n{self._get_summary()}\n\nOlder conversation:\n{old_conv}"),
        ]

        summary = str(self.llm.invoke(prompt).content).strip()[:SUMMARY_MAX_CHARS]
        self._save_summary(summary)

    def _generate_title(self, question: str) -> None:
        """Asks the LLM to summarize the first prompt into a short title."""
        prompt = [
            SystemMessage(content="You are a helpful assistant. Summarize this user prompt into a short title of 3-5 words for a chat history menu. Do not use quotes, punctuation, or filler words. Output only the title."),
            HumanMessage(content=question)
        ]
        title = str(self.llm.invoke(prompt).content).strip()
        # Clean up any quotes the LLM might stubbornly include
        title = title.replace('"', '').replace("'", "")
        
        ChatApplication.rename_conversation(str(self.user_id), str(self.conversation_id), title)

    def chat(self, question: str) -> str:
        if not (question := question.strip()):
            raise ValueError("Question cannot be empty.")

        summary, recent_messages = self._build_context()
        prompt = [SystemMessage(content=SYSTEM_PROMPT)]

        if summary:
            prompt.append(
                SystemMessage(content=f"Important information remembered from earlier:\n\n{summary}")
            )

        prompt.extend(recent_messages)
        prompt.append(HumanMessage(content=question))

        answer = str(self.llm.invoke(prompt).content).strip()
        self._save_exchange(question, answer)

        # TRIGGER 1: Generate title if this is the very first exchange
        if self._get_message_count() == 2:
            self._generate_title(question)

        # TRIGGER 2: Generate memory summary periodically
        if self._get_message_count() % SUMMARY_TRIGGER_MESSAGES == 0:
            self._create_summary()

        return answer

    def get_messages_for_ui(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT role, content
                    FROM messages
                    WHERE conversation_id = :conversation_id
                      AND EXISTS (
                          SELECT 1 FROM conversations
                          WHERE id = :conversation_id AND user_id = :user_id
                      )
                    ORDER BY created_at ASC, id ASC
                """),
                {"conversation_id": self.conversation_id, "user_id": self.user_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def clear_history(self) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    DELETE FROM messages
                    WHERE conversation_id = :id
                      AND EXISTS (
                          SELECT 1 FROM conversations
                          WHERE id = :id AND user_id = :user_id
                      )
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            )
            if result.rowcount == 0:
                # A legitimate empty conversation is allowed; verify ownership separately.
                exists = conn.execute(
                    text("""
                        SELECT 1 FROM conversations
                        WHERE id = :id AND user_id = :user_id
                    """),
                    {"id": self.conversation_id, "user_id": self.user_id},
                ).first()
                if not exists:
                    raise ConversationNotFoundError("Conversation not found.")

            conn.execute(
                text("""
                    UPDATE conversations
                    SET summary = '', updated_at = NOW()
                    WHERE id = :id AND user_id = :user_id
                """),
                {"id": self.conversation_id, "user_id": self.user_id},
            )

    @classmethod
    def list_conversations(cls, user_id: str) -> list[dict]:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, title, summary, created_at, updated_at
                    FROM conversations
                    WHERE user_id = :user_id
                    ORDER BY updated_at DESC, created_at DESC
                """),
                {"user_id": UUID(user_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    @classmethod
    def create_conversation(cls, user_id: str) -> str:
        conversation_id = uuid4()
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO conversations (id, user_id, title)
                    VALUES (:id, :user_id, 'New Conversation')
                """),
                {"id": conversation_id, "user_id": UUID(user_id)},
            )
        return str(conversation_id)

    @classmethod
    def rename_conversation(cls, user_id: str, conversation_id: str, title: str) -> None:
        title = title.strip()[:500]
        if not title:
            raise ValueError("Conversation title cannot be empty.")

        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE conversations
                    SET title = :title, updated_at = NOW()
                    WHERE id = :conversation_id AND user_id = :user_id
                """),
                {
                    "title": title,
                    "conversation_id": UUID(conversation_id),
                    "user_id": UUID(user_id),
                },
            )
            if result.rowcount != 1:
                raise ConversationNotFoundError("Conversation not found.")
