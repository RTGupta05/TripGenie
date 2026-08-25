from uuid import UUID, uuid4

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from .config import (
    DATABASE_URL, RECENT_MESSAGE_COUNT, SUMMARY_TRIGGER_MESSAGES, 
    SUMMARY_MAX_CHARS, MAX_PROMPT_CHARS
)
from .llm import create_llm

SYSTEM_PROMPT = "You are an expert detail oriented global travel planner and local insider. Give crisp and concise answers."

class ChatApplication:
    def __init__(self, conversation_id: str | None = None):
        self.llm = create_llm()
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.conversation_id = UUID(conversation_id) if conversation_id else uuid4()
        self._initialize_database()
        self._ensure_conversation()
    
    def _initialize_database(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY,
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
            """))

    def _ensure_conversation(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO conversations (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
                {"id": self.conversation_id}
            )

    def get_conversation_id(self) -> str:
        return str(self.conversation_id)

    # =====================================================
    # SUMMARY
    # =====================================================
    def _get_summary(self) -> str:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT summary FROM conversations WHERE id = :id"),
                {"id": self.conversation_id}
            ).mappings().first()
            return row["summary"] if row and row["summary"] else ""

    def _save_summary(self, summary: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE conversations SET summary = :summary, updated_at = NOW() WHERE id = :id"),
                {"id": self.conversation_id, "summary": summary}
            )

    # =====================================================
    # MESSAGES
    # =====================================================
    def _get_recent_messages(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT role, content FROM messages 
                    WHERE conversation_id = :id 
                    ORDER BY created_at DESC, id DESC LIMIT :limit
                """),
                {"id": self.conversation_id, "limit": RECENT_MESSAGE_COUNT}
            ).mappings().all()

        return [
            HumanMessage(content=r["content"]) if r["role"] == "human" else AIMessage(content=r["content"])
            for r in reversed(rows)
        ]

    def _save_message(self, role: str, content: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO messages (conversation_id, role, content) VALUES (:id, :role, :content)"),
                {"id": self.conversation_id, "role": role, "content": content}
            )
            conn.execute(
                text("UPDATE conversations SET updated_at = NOW() WHERE id = :id"),
                {"id": self.conversation_id}
            )

    def _save_exchange(self, question: str, answer: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO messages (conversation_id, role, content) VALUES (:id, 'human', :q), (:id, 'ai', :a)"),
                {"id": self.conversation_id, "q": question, "a": answer}
            )
            conn.execute(
                text("UPDATE conversations SET updated_at = NOW() WHERE id = :id"),
                {"id": self.conversation_id}
            )

    def _get_message_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE conversation_id = :id"),
                {"id": self.conversation_id}
            ).scalar_one()
    # =====================================================
    # CONTEXT MANAGEMENT
    # =====================================================

    def _trim_recent_messages(self, messages):
        if not messages: return []
        
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

    # =====================================================
    # SUMMARY GENERATION
    # =====================================================

    def _get_messages_for_summary(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, role, content FROM messages WHERE conversation_id = :id ORDER BY created_at ASC, id ASC"),
                {"id": self.conversation_id}
            ).mappings().all()
            
        return rows[:-RECENT_MESSAGE_COUNT] if len(rows) > RECENT_MESSAGE_COUNT else []

    def _create_summary(self) -> None:
        if not (old_messages := self._get_messages_for_summary()):
            return

        parts = [f"{'User' if m['role'] == 'human' else 'Assistant'}: {m['content']}" for m in old_messages]
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
            HumanMessage(content=f"Existing memory:\n{self._get_summary()}\n\nOlder conversation:\n{old_conv}")
        ]

        summary = str(self.llm.invoke(prompt).content).strip()[:SUMMARY_MAX_CHARS]
        self._save_summary(summary)
        print(summary)

    # =====================================================
    # CHAT
    # =====================================================

    def chat(self, question: str) -> str:
        if not (question := question.strip()):
            raise ValueError("Question cannot be empty.")

        summary, recent_messages = self._build_context()
        prompt = [SystemMessage(content=SYSTEM_PROMPT)]
        
        if summary:
            prompt.append(SystemMessage(content=f"Important information remembered from earlier:\n\n{summary}"))
            
        prompt.extend(recent_messages)
        prompt.append(HumanMessage(content=question))

        answer = str(self.llm.invoke(prompt).content).strip()
        self._save_exchange(question, answer)

        if self._get_message_count() % SUMMARY_TRIGGER_MESSAGES == 0:
            self._create_summary()

        return answer

    # =====================================================
    # CLEAR CONVERSATION
    # =====================================================

    def clear_history(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM messages WHERE conversation_id = :id"),
                {"id": self.conversation_id}
            )
            conn.execute(
                text("UPDATE conversations SET summary = '', updated_at = NOW() WHERE id = :id"),
                {"id": self.conversation_id}
            )