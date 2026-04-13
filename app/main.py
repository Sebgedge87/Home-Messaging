from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from pywebpush import WebPushException, webpush

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "messaging.db"

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-insecure-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24 * 14
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_SUBJECT = os.getenv("VAPID_CLAIMS_SUBJECT", "mailto:admin@example.com")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Home Messaging")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, WebSocket] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[user_id] = websocket

    def disconnect(self, user_id: int) -> None:
        self.connections.pop(user_id, None)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead = []
        for uid, ws in self.connections.items():
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.disconnect(uid)


manager = ConnectionManager()


class AuthRequest(BaseModel):
    username: str
    password: str
    invite_code: str | None = None


class InviteResponse(BaseModel):
    code: str


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                created_by INTEGER NOT NULL,
                used_by INTEGER,
                created_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY(created_by) REFERENCES users(id),
                FOREIGN KEY(used_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                text TEXT,
                audio_url TEXT,
                transcript TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def create_token(user_id: int, username: str, is_admin: bool) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    payload = {"sub": str(user_id), "username": username, "is_admin": is_admin, "exp": exp}
    return jwt.encode(payload, APP_SECRET_KEY, algorithm=ALGORITHM)


def parse_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, APP_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    payload = parse_token(token)
    return {
        "id": int(payload["sub"]),
        "username": payload["username"],
        "is_admin": bool(payload["is_admin"]),
    }


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, str]:
    return {"vapidPublicKey": VAPID_PUBLIC_KEY}


@app.post("/api/register")
def register(body: AuthRequest) -> dict[str, Any]:
    with db() as conn:
        user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        is_first_user = user_count == 0

        if not is_first_user:
            if not body.invite_code:
                raise HTTPException(status_code=400, detail="Invite code required")
            invite = conn.execute(
                "SELECT * FROM invites WHERE code = ? AND used_by IS NULL", (body.invite_code,)
            ).fetchone()
            if not invite:
                raise HTTPException(status_code=400, detail="Invalid or used invite code")

        password_hash = pwd_context.hash(body.password)
        now = datetime.now(timezone.utc).isoformat()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (body.username.strip(), password_hash, 1 if is_first_user else 0, now),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=400, detail="Username already exists") from exc

        user_id = cur.lastrowid
        if not is_first_user:
            conn.execute(
                "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?",
                (user_id, now, body.invite_code),
            )

        token = create_token(user_id, body.username, is_first_user)
        return {"token": token, "username": body.username, "is_admin": is_first_user}


@app.post("/api/login")
def login(body: AuthRequest) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
        if not row or not pwd_context.verify(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(row["id"], row["username"], bool(row["is_admin"]))
        return {"token": token, "username": row["username"], "is_admin": bool(row["is_admin"]) }


@app.post("/api/invites", response_model=InviteResponse)
def create_invite(user: dict[str, Any] = Depends(current_user)) -> InviteResponse:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    code = secrets.token_urlsafe(8)
    with db() as conn:
        conn.execute(
            "INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)",
            (code, user["id"], datetime.now(timezone.utc).isoformat()),
        )
    return InviteResponse(code=code)


@app.get("/api/messages")
def list_messages(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    del user
    with db() as conn:
        rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(row) for row in reversed(rows)]


@app.post("/api/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    transcript: str = Form(default=""),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, str]:
    suffix = Path(file.filename or "voice.webm").suffix or ".webm"
    filename = f"voice_{user['id']}_{int(datetime.now().timestamp())}{suffix}"
    path = UPLOAD_DIR / filename
    with path.open("wb") as f:
        f.write(await file.read())

    audio_url = f"/uploads/{filename}"
    with db() as conn:
        conn.execute(
            "INSERT INTO messages (user_id, username, text, audio_url, transcript, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                user["id"],
                user["username"],
                None,
                audio_url,
                transcript,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    payload = {
        "type": "message",
        "username": user["username"],
        "text": None,
        "audio_url": audio_url,
        "transcript": transcript,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(payload)
    send_push_to_all(user["id"], f"{user['username']} sent a voice note")
    return {"audio_url": audio_url}


@app.post("/api/subscribe")
def subscribe(subscription: dict[str, Any], user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys", {})
    if not endpoint or "p256dh" not in keys or "auth" not in keys:
        raise HTTPException(status_code=400, detail="Invalid subscription")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth
            """,
            (user["id"], endpoint, keys["p256dh"], keys["auth"], datetime.now(timezone.utc).isoformat()),
        )
    return {"status": "subscribed"}


@app.websocket("/ws")
async def ws_chat(websocket: WebSocket, token: str) -> None:
    payload = parse_token(token)
    user = {"id": int(payload["sub"]), "username": payload["username"]}
    await manager.connect(user["id"], websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            text = (data.get("text") or "").strip()
            if not text:
                continue

            now = datetime.now(timezone.utc).isoformat()
            with db() as conn:
                conn.execute(
                    "INSERT INTO messages (user_id, username, text, audio_url, transcript, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], user["username"], text, None, None, now),
                )

            payload = {
                "type": "message",
                "username": user["username"],
                "text": text,
                "audio_url": None,
                "transcript": None,
                "created_at": now,
            }
            await manager.broadcast(payload)
            send_push_to_all(user["id"], f"{user['username']}: {text[:60]}")
    except WebSocketDisconnect:
        manager.disconnect(user["id"])


def send_push_to_all(sender_user_id: int, message: str) -> None:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return

    with db() as conn:
        rows = conn.execute("SELECT * FROM subscriptions WHERE user_id != ?", (sender_user_id,)).fetchall()

    payload = json.dumps({"title": "Home Messaging", "body": message})
    for row in rows:
        subscription_info = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_SUBJECT},
            )
        except WebPushException:
            pass
