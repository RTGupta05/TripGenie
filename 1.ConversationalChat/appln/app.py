from __future__ import annotations

import html
import os
from pathlib import Path
import sys
import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auth import AuthService, AuthenticationError
from chat import ChatApplication, ConversationNotFoundError
from config import SESSION_COOKIE_NAME


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """Require an application session for the mounted Gradio UI."""

    def __init__(self, app, auth_service: AuthService):
        super().__init__(app)
        self.auth_service = auth_service

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/app") and path not in {"/app"}:
            session_id = request.cookies.get(SESSION_COOKIE_NAME)
            if not self.auth_service.get_user_from_session(session_id):
                if path.startswith("/app/queue/"):
                    return HTMLResponse("Authentication required", status_code=401)
                return RedirectResponse("/login", status_code=303)
        return await call_next(request)


def _login_page(error: str = "") -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""
    <!doctype html>
    <html><head><title>Travel Planner - Login</title>
    <style>
      body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 80px auto; padding: 20px; }}
      input {{ width: 100%; box-sizing: border-box; padding: 12px; margin: 8px 0; }}
      button {{ width: 100%; padding: 12px; margin-top: 10px; cursor: pointer; }}
      .error {{ color: #b00020; margin-bottom: 12px; }}
      a {{ display: block; margin-top: 18px; text-align: center; }}
    </style></head>
    <body>
      <h1>Personal Travel Planner</h1>
      <h2>Login</h2>
      {error_html}
      <form method="post" action="/login">
        <input name="email" type="email" placeholder="Email" required>
        <input name="password" type="password" placeholder="Password" required>
        <button type="submit">Login</button>
      </form>
      <a href="/register">Create an account</a>
    </body></html>
    """


def _register_page(error: str = "") -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""
    <!doctype html>
    <html><head><title>Travel Planner - Register</title>
    <style>
      body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 80px auto; padding: 20px; }}
      input {{ width: 100%; box-sizing: border-box; padding: 12px; margin: 8px 0; }}
      button {{ width: 100%; padding: 12px; margin-top: 10px; cursor: pointer; }}
      .error {{ color: #b00020; margin-bottom: 12px; }}
      a {{ display: block; margin-top: 18px; text-align: center; }}
    </style></head>
    <body>
      <h1>Personal Travel Planner</h1>
      <h2>Create account</h2>
      {error_html}
      <form method="post" action="/register">
        <input name="name" type="text" placeholder="Name" required>
        <input name="email" type="email" placeholder="Email" required>
        <input name="password" type="password" placeholder="Password (min 8 characters)" required>
        <button type="submit">Create account</button>
      </form>
      <a href="/login">Back to login</a>
    </body></html>
    """


def _current_user(request: gr.Request, auth: AuthService) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE_NAME) if request else None
    user = auth.get_user_from_session(session_id)
    if not user:
        raise gr.Error("Your session has expired. Please log in again.")
    return user


def build_gradio(auth: AuthService) -> gr.Blocks:
    # Custom CSS for the Gemini-style scrollable sidebar
    custom_css = """
    .recent-chats-container {
        max-height: 520px;
        overflow-y: auto;
        padding-right: 6px;
    }
    .recent-chats-container::-webkit-scrollbar {
        width: 6px;
    }
    .recent-chats-container::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 4px;
    }
    /* Style the radio items into modern clickable list cards */
    .recent-chats-container label {
        display: block !important;
        background: #f8fafc !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
        padding: 10px 14px !important;
        margin-bottom: 8px !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
    }
    .recent-chats-container label:hover {
        background: #f1f5f9 !important;
        border-color: #cbd5e1 !important;
    }
    .recent-chats-container label.selected {
        background: #e0e7ff !important;
        border-color: #6366f1 !important;
        font-weight: 600 !important;
    }
    .recent-chats-container input[type="radio"] {
        display: none !important; /* Hide default radio circle */
    }
    """

    with gr.Blocks(title="Personal Travel Planner", css=custom_css) as demo:
        conversation_state = gr.State(value=None)

        with gr.Row():
            gr.Markdown("# ✈ Personal Travel Planner")
            logout = gr.Button("Logout", scale=0, variant="secondary")
        logout_result = gr.HTML(visible=False)

        with gr.Row():
            # --- REQUIREMENT 2: SIDEBAR (Gemini Style Recents) ---
            with gr.Column(scale=1, min_width=280):
                user_info = gr.Markdown("Loading user...")
                new_chat = gr.Button("+ New chat", variant="primary")
                
                gr.Markdown("### **Recents**")
                with gr.Group(elem_classes=["recent-chats-container"]):
                    conversations = gr.Radio(
                        label="",
                        choices=[],
                        value=None,
                        interactive=True,
                        show_label=False,
                    )
                
                refresh = gr.Button("🔄 Refresh list", variant="secondary", size="sm")

            with gr.Column(scale=3):
                chat = gr.Chatbot(
                    label="Conversation",
                    height=600,
                    autoscroll=True,
                )
                question = gr.Textbox(
                    label="",
                    placeholder="Ask about your trip...",
                    lines=2,
                )
                send = gr.Button("Send", variant="primary")

        def get_chat_choices(user_id: str):
            items = ChatApplication.list_conversations(user_id)
            # Format choices as (Label/Title, Value/UUID)
            return [(item["title"] or "New Conversation", str(item["id"])) for item in items]

        def load_user(request: gr.Request):
            user = _current_user(request, auth)
            
            # REQUIREMENT 3: Start fresh with a completely new conversation on login
            new_id = ChatApplication.create_conversation(user["id"])
            choices = get_chat_choices(user["id"])
            
            return (
                f"**Signed in as:** {user['name']}",
                gr.Radio(choices=choices, value=new_id),
                [], # Empty message list for new chat
                new_id,
            )

        def create_chat(request: gr.Request):
            user = _current_user(request, auth)
            conversation_id = ChatApplication.create_conversation(user["id"])
            choices = get_chat_choices(user["id"])
            return gr.Radio(choices=choices, value=conversation_id), [], conversation_id

        def select_chat(conversation_id: str, request: gr.Request):
            if not conversation_id:
                return [], None
            user = _current_user(request, auth)
            try:
                chatbot = ChatApplication(user["id"], conversation_id)
            except ConversationNotFoundError:
                raise gr.Error("Conversation not found or access denied.")
                
            messages = [
                {
                    "role": "user" if row["role"] == "human" else "assistant",
                    "content": row["content"],
                }
                for row in chatbot.get_messages_for_ui()
            ]
            return messages, conversation_id

        def send_message(message: str, history: list, conversation_id: str, request: gr.Request):
            user = _current_user(request, auth)
            if not conversation_id:
                conversation_id = ChatApplication.create_conversation(user["id"])

            chatbot = ChatApplication(user["id"], conversation_id)
            answer = chatbot.chat(message)
            
            updated_history = list(history or [])
            updated_history.append({"role": "user", "content": message})
            updated_history.append({"role": "assistant", "content": answer})
            
            # Fetch updated choices so the sidebar immediately shows the newly generated title
            choices = get_chat_choices(user["id"])
            
            return updated_history, "", conversation_id, gr.Radio(choices=choices, value=conversation_id)

        def refresh_chats(request: gr.Request):
            user = _current_user(request, auth)
            choices = get_chat_choices(user["id"])
            return gr.Radio(choices=choices)

        def do_logout(request: gr.Request):
            session_id = request.cookies.get(SESSION_COOKIE_NAME) if request else None
            auth.revoke_session(session_id)
            return gr.HTML("<script>window.location.href='/logout'</script>")

        # UI Event Listeners
        demo.load(
            load_user,
            outputs=[user_info, conversations, chat, conversation_state],
        )
        new_chat.click(
            create_chat,
            outputs=[conversations, chat, conversation_state],
        )
        # Click-to-switch behavior is handled here
        conversations.change(
            select_chat,
            inputs=[conversations],
            outputs=[chat, conversation_state],
        )
        refresh.click(refresh_chats, outputs=[conversations])
        # Include 'conversations' in the outputs to automatically update the title
        send.click(
            send_message,
            inputs=[question, chat, conversation_state],
            outputs=[chat, question, conversation_state, conversations],
        )
        question.submit(
            send_message,
            inputs=[question, chat, conversation_state],
            outputs=[chat, question, conversation_state, conversations],
        )
        logout.click(do_logout, outputs=logout_result)

    return demo


def create_app() -> FastAPI:
    auth = AuthService()
    app = FastAPI(title="Personal Travel Planner")
    app.add_middleware(LoginRequiredMiddleware, auth_service=auth)

    @app.get("/", include_in_schema=False)
    async def root(request: Request):
        if auth.get_user_from_session(request.cookies.get(SESSION_COOKIE_NAME)):
            return RedirectResponse("/app", status_code=303)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page():
        return _login_page()

    @app.post("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(request: Request):
        form = await request.form()
        try:
            user = auth.authenticate(str(form.get("email", "")), str(form.get("password", "")))
        except AuthenticationError as exc:
            return HTMLResponse(_login_page(str(exc)), status_code=401)

        session_id = auth.create_session(user["id"])
        response = RedirectResponse("/app", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            samesite="lax",
            max_age=60 * 60 * int(os.getenv("SESSION_TTL_HOURS", "24")),
        )
        return response

    @app.get("/register", response_class=HTMLResponse, include_in_schema=False)
    async def register_page():
        return _register_page()

    @app.post("/register", response_class=HTMLResponse, include_in_schema=False)
    async def register(request: Request):
        form = await request.form()
        try:
            user_id = auth.create_user(
                str(form.get("email", "")),
                str(form.get("password", "")),
                str(form.get("name", "")),
            )
        except ValueError as exc:
            return HTMLResponse(_register_page(str(exc)), status_code=400)

        session_id = auth.create_session(user_id)
        response = RedirectResponse("/app", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            samesite="lax",
            max_age=60 * 60 * int(os.getenv("SESSION_TTL_HOURS", "24")),
        )
        return response

    @app.get("/logout", include_in_schema=False)
    async def logout(request: Request):
        auth.revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response

    demo = build_gradio(auth)
    app = gr.mount_gradio_app(app, demo, path="/app")
    return app


app = create_app()
if __name__ == "__main__":
    import uvicorn
    # This will start the FastAPI server on port 8000 when the script is run directly
    print("Starting Gradio UI on http://localhost:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
