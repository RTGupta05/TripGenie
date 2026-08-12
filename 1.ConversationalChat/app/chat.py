from langchain_core.messages import (
    HumanMessage,
    AIMessage,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)

from .llm import create_llm


class ChatApplication:

    def __init__(self):

        self.llm = create_llm()

        self.prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "You are an expert detail oriented golbal travel planner and local insider. "
                "Give crisp and concise answers."
            ),
            MessagesPlaceholder(
                variable_name="history"
            ),
            (
                "human",
                "{question}"
            ),
        ])

        # LCEL
        self.chain = self.prompt | self.llm

        self.history = []

    def chat(self, question: str) -> str:

        response = self.chain.invoke({
            "history": self.history,
            "question": question,
        })

        self.history.append(
            HumanMessage(
                content=question
            )
        )

        self.history.append(
            AIMessage(
                content=response.content
            )
        )

        return response.content

    def clear_history(self):

        self.history = []