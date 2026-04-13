from __future__ import annotations

import json
import os
import secrets
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from pywebpush import WebPushException, webpush
from cryptography.fernet import Fernet
import base64
import hashlib

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "messaging.db"

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-insecure-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24 * 14
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_SUBJECT = os.getenv("VAPID_CLAIMS_SUBJECT", "mailto:admin@example.com")
OWNER_SETUP_KEY = os.getenv("OWNER_SETUP_KEY", "")

_key_bytes = hashlib.sha256(APP_SECRET_KEY.encode()).digest()
crypto_key = base64.urlsafe_b64encode(_key_bytes)
fernet = Fernet(crypto_key)

def encrypt_text(text: str | None) -> str | None:
    if not text: return text
    return fernet.encrypt(text.encode('utf-8')).decode('utf-8')

def decrypt_text(text: str | None) -> str | None:
    if not text: return text
    try: return fernet.decrypt(text.encode('utf-8')).decode('utf-8')
    except Exception: return text  # fallback for plain text

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
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

    async def send_many(self, user_ids: set[int], payload: dict[str, Any]) -> None:
        dead = []
        for uid, ws in self.connections.items():
            if uid not in user_ids:
                continue
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
    owner_key: str | None = None


class InviteResponse(BaseModel):
    code: str


class GroupCreate(BaseModel):
    name: str
    is_broadcast: bool = False


class GroupMemberAdd(BaseModel):
    username: str


class PasswordResetRequest(BaseModel):
    new_password: str

class SettingsUpdate(BaseModel):
    theme_color: str | None = None
    wallpaper_url: str | None = None
    font_family: str | None = None
    font_size: str | None = None
    theme_bg: str | None = None
    theme_text: str | None = None
    theme_theirs: str | None = None



def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def ensure_general_group(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM groups WHERE name='General'").fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO groups (name, created_by, is_broadcast, created_at) VALUES (?, ?, ?, ?)",
        ("General", 0, 0, datetime.now(timezone.utc).isoformat()),
    )
    gid = int(cur.lastrowid)
    users = conn.execute("SELECT id FROM users").fetchall()
    for u in users:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
            (gid, u["id"], datetime.now(timezone.utc).isoformat()),
        )
    return gid


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

            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_by INTEGER NOT NULL,
                is_broadcast INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                UNIQUE(group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                text TEXT,
                audio_url TEXT,
                transcript TEXT,
                gif_url TEXT,
                is_intercom INTEGER DEFAULT 0,
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
        if not column_exists(conn, "messages", "group_id"):
            conn.execute("ALTER TABLE messages ADD COLUMN group_id INTEGER")
        if not column_exists(conn, "messages", "parent_id"):
            conn.execute("ALTER TABLE messages ADD COLUMN parent_id INTEGER")
        if not column_exists(conn, "messages", "gif_url"):
            conn.execute("ALTER TABLE messages ADD COLUMN gif_url TEXT")
        if not column_exists(conn, "messages", "is_intercom"):
            conn.execute("ALTER TABLE messages ADD COLUMN is_intercom INTEGER DEFAULT 0")
        if not column_exists(conn, "users", "theme_color"):
            conn.execute("ALTER TABLE users ADD COLUMN theme_color TEXT DEFAULT '#5b8cff'")
        if not column_exists(conn, "users", "wallpaper_url"):
            conn.execute("ALTER TABLE users ADD COLUMN wallpaper_url TEXT")
        if not column_exists(conn, "users", "font_family"):
            conn.execute("ALTER TABLE users ADD COLUMN font_family TEXT DEFAULT 'Inter'")
        if not column_exists(conn, "users", "font_size"):
            conn.execute("ALTER TABLE users ADD COLUMN font_size TEXT DEFAULT '15px'")
        if not column_exists(conn, "users", "theme_bg"):
            conn.execute("ALTER TABLE users ADD COLUMN theme_bg TEXT DEFAULT '#09090b'")
        if not column_exists(conn, "users", "theme_text"):
            conn.execute("ALTER TABLE users ADD COLUMN theme_text TEXT DEFAULT '#f4f4f5'")
        if not column_exists(conn, "users", "theme_theirs"):
            conn.execute("ALTER TABLE users ADD COLUMN theme_theirs TEXT DEFAULT '#18181b'")

        general_id = ensure_general_group(conn)
        users = conn.execute("SELECT id FROM users").fetchall()
        for u in users:
            conn.execute(
                "INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
                (general_id, u["id"], datetime.now(timezone.utc).isoformat()),
            )


@app.on_event("startup")
def startup() -> None:
    print(f"=== IMPORTANT SYSTEM CHECK ===", flush=True)
    print(f"Resolved DATA_DIR: {DATA_DIR.absolute()}", flush=True)
    print(f"Resolved DB_PATH: {DB_PATH.absolute()}", flush=True)
    print(f"============================", flush=True)
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
    payload = parse_token(authorization.removeprefix("Bearer ").strip())
    return {"id": int(payload["sub"]), "username": payload["username"], "is_admin": bool(payload["is_admin"])}


def get_visible_group_ids(conn: sqlite3.Connection, user: dict[str, Any]) -> set[int]:
    rows = conn.execute(
        """
        SELECT g.id
        FROM groups g
        LEFT JOIN group_members gm ON gm.group_id = g.id AND gm.user_id = ?
        WHERE gm.user_id IS NOT NULL OR g.is_broadcast = 1
        """,
        (user["id"],),
    ).fetchall()
    return {int(r["id"]) for r in rows}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


import urllib.request
import re
from urllib.parse import urlparse

@app.get("/api/proxy-image")
def proxy_image(url: str = Query(...)):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req)
        content_type = resp.headers.get_content_type()
        
        if content_type.startswith("text/html"):
            html = resp.read().decode('utf-8', errors='ignore')
            match = re.search(r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
            if not match:
                match = re.search(r'id=["\']wallpaper["\'][^>]+src=["\']([^"\']+)["\']', html)
            if not match:
                match = re.search(r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png))["\']', html, re.I)
            
            if match:
                img_url = match.group(1).replace('&amp;', '&')
                if img_url.startswith("/"):
                    parsed = urlparse(url)
                    img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
                elif not img_url.startswith("http"):
                    img_url = "https:" + img_url if img_url.startswith("//") else "https://" + img_url
                    
                req2 = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                resp2 = urllib.request.urlopen(req2)
                content_type2 = resp2.headers.get_content_type()
                
                def iterfile2():
                    while chunk := resp2.read(8192):
                        yield chunk
                return StreamingResponse(iterfile2(), media_type=content_type2)
            else:
                raise HTTPException(status_code=400, detail="Could not find an image in that webpage.")
                
        def iterfile():
            while chunk := resp.read(8192):
                yield chunk
        return StreamingResponse(iterfile(), media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/config")
def config() -> dict[str, str]:
    return {"vapidPublicKey": VAPID_PUBLIC_KEY}


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT theme_color, wallpaper_url, font_family, font_size, theme_bg, theme_text, theme_theirs FROM users WHERE id = ?", (user["id"],)).fetchone()
    d = dict(row) if row else {}
    return {**user, "theme_color": d.get("theme_color", "#5b8cff"), "wallpaper_url": d.get("wallpaper_url"), "font_family": d.get("font_family", "Inter"), "font_size": d.get("font_size", "15px"), "theme_bg": d.get("theme_bg", "#09090b"), "theme_text": d.get("theme_text", "#f4f4f5"), "theme_theirs": d.get("theme_theirs", "#18181b")}

@app.post("/api/settings")
def update_settings(body: SettingsUpdate, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    with db() as conn:
        conn.execute("UPDATE users SET theme_color = ?, wallpaper_url = ?, font_family = ?, font_size = ?, theme_bg = ?, theme_text = ?, theme_theirs = ? WHERE id = ?", (body.theme_color, body.wallpaper_url, body.font_family, body.font_size, body.theme_bg, body.theme_text, body.theme_theirs, user["id"]))
    return {"status": "updated"}


@app.get("/api/bootstrap")
def bootstrap_status() -> dict[str, bool]:
    with db() as conn:
        user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    return {"has_users": user_count > 0}


@app.post("/api/register")
def register(body: AuthRequest) -> dict[str, Any]:
    with db() as conn:
        user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        is_first_user = user_count == 0
        owner_override = bool(OWNER_SETUP_KEY and body.owner_key and secrets.compare_digest(body.owner_key, OWNER_SETUP_KEY))

        if not is_first_user and not owner_override:
            if not body.invite_code:
                raise HTTPException(status_code=400, detail="Invite code required")
            invite = conn.execute("SELECT * FROM invites WHERE code = ? AND used_by IS NULL", (body.invite_code,)).fetchone()
            if not invite:
                raise HTTPException(status_code=400, detail="Invalid or used invite code")

        now = datetime.now(timezone.utc).isoformat()
        username_norm = body.username.strip().lower()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (username_norm, pwd_context.hash(body.password), 1 if (is_first_user or owner_override) else 0, now),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=400, detail="Username already exists") from exc

        user_id = int(cur.lastrowid)
        general_id = ensure_general_group(conn)
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
            (general_id, user_id, now),
        )

        if not is_first_user and not owner_override:
            conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?", (user_id, now, body.invite_code))

        is_admin = is_first_user or owner_override
        token = create_token(user_id, username_norm, is_admin)
        return {"token": token, "username": username_norm, "is_admin": is_admin, "theme_color": "#5b8cff", "wallpaper_url": None, "font_family": "Inter", "font_size": "15px", "theme_bg": "#09090b", "theme_text": "#f4f4f5", "theme_theirs": "#18181b"}


@app.post("/api/login")
def login(body: AuthRequest) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip().lower(),)).fetchone()
        if not row or not pwd_context.verify(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(row["id"], row["username"], bool(row["is_admin"]))
        d = dict(row)
        return {"token": token, "username": d["username"], "is_admin": bool(d["is_admin"]), "theme_color": d.get("theme_color", "#5b8cff"), "wallpaper_url": d.get("wallpaper_url"), "font_family": d.get("font_family", "Inter"), "font_size": d.get("font_size", "15px"), "theme_bg": d.get("theme_bg", "#09090b"), "theme_text": d.get("theme_text", "#f4f4f5"), "theme_theirs": d.get("theme_theirs", "#18181b")}


@app.post("/api/invites", response_model=InviteResponse)
def create_invite(user: dict[str, Any] = Depends(current_user)) -> InviteResponse:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    code = secrets.token_urlsafe(8)
    with db() as conn:
        conn.execute("INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)", (code, user["id"], datetime.now(timezone.utc).isoformat()))
    return InviteResponse(code=code)


@app.get("/api/groups")
def list_groups(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT g.*,
                   CASE WHEN gm.user_id IS NULL THEN 0 ELSE 1 END AS is_member
            FROM groups g
            LEFT JOIN group_members gm ON gm.group_id = g.id AND gm.user_id = ?
            WHERE gm.user_id IS NOT NULL OR g.is_broadcast = 1
            ORDER BY g.name
            """,
            (user["id"],),
        ).fetchall()

        out = []
        for r in rows:
            d = dict(r)
            if d["name"].startswith("#direct_"):
                parts = d["name"].split("_")
                try:
                    p1, p2 = int(parts[1]), int(parts[2])
                    other_id = p2 if p1 == user["id"] else p1
                    other_user = conn.execute("SELECT username FROM users WHERE id = ?", (other_id,)).fetchone()
                    if other_user:
                        d["name"] = f"Direct: {other_user['username']}"
                except Exception:
                    pass
            out.append(d)
    return out

@app.get("/api/contacts")
def list_contacts(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT id, username FROM users WHERE id != ? ORDER BY username", (user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/direct/{target_user_id}")
def start_direct(target_user_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if target_user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot message yourself")
        
    u1, u2 = min(user["id"], target_user_id), max(user["id"], target_user_id)
    group_name = f"#direct_{u1}_{u2}"
    now = datetime.now(timezone.utc).isoformat()
    
    with db() as conn:
        target = conn.execute("SELECT id FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        
        row = conn.execute("SELECT id FROM groups WHERE name = ?", (group_name,)).fetchone()
        if row:
            gid = int(row["id"])
            conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)", (gid, user["id"], now))
            conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)", (gid, target_user_id, now))
            return {"id": gid}
        
        cur = conn.execute(
            "INSERT INTO groups (name, created_by, is_broadcast, created_at) VALUES (?, ?, ?, ?)",
            (group_name, user["id"], 0, now),
        )
        gid = int(cur.lastrowid)
        conn.execute("INSERT INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)", (gid, user["id"], now))
        conn.execute("INSERT INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)", (gid, target_user_id, now))
        return {"id": gid}


@app.post("/api/groups")
def create_group(body: GroupCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO groups (name, created_by, is_broadcast, created_at) VALUES (?, ?, ?, ?)",
            (body.name.strip(), user["id"], 1 if body.is_broadcast else 0, now),
        )
        gid = int(cur.lastrowid)
        conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)", (gid, user["id"], now))
        return {"id": gid, "name": body.name.strip(), "is_broadcast": body.is_broadcast}


@app.post("/api/groups/{group_id}/members")
def add_member(group_id: int, body: GroupMemberAdd, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    with db() as conn:
        target = conn.execute("SELECT id FROM users WHERE username = ?", (body.username.strip().lower(),)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
            (group_id, target["id"], datetime.now(timezone.utc).isoformat()),
        )
    return {"status": "added"}


@app.get("/api/messages")
def list_messages(group_id: int = Query(...), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as conn:
        visible = get_visible_group_ids(conn, user)
        if group_id not in visible:
            raise HTTPException(status_code=403, detail="Not allowed in this group")
        rows = conn.execute(
            "SELECT * FROM messages WHERE group_id = ? ORDER BY id DESC LIMIT 300",
            (group_id,),
        ).fetchall()
    
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["text"] = decrypt_text(d["text"])
        d["transcript"] = decrypt_text(d["transcript"])
        out.append(d)
    return out

@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    with db() as conn:
        msg = conn.execute("SELECT user_id, group_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        if not user["is_admin"] and int(msg["user_id"]) != user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this message")
        
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        recipients = get_group_user_ids(conn, int(msg["group_id"]))
        
    await manager.send_many(recipients, {"type": "message_deleted", "id": message_id})
    return {"status": "deleted"}


@app.post("/api/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    group_id: int = Form(...),
    transcript: str = Form(default=""),
    parent_id: int | None = Form(default=None),
    is_intercom: int = Form(default=0),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, str]:
    with db() as conn:
        visible = get_visible_group_ids(conn, user)
        if group_id not in visible:
            raise HTTPException(status_code=403, detail="Not allowed in this group")

    suffix = Path(file.filename or "voice.webm").suffix or ".webm"
    filename = f"voice_{user['id']}_{int(datetime.now().timestamp())}{suffix}"
    path = UPLOAD_DIR / filename
    path.write_bytes(await file.read())

    now = datetime.now(timezone.utc).isoformat()
    audio_url = f"/uploads/{filename}"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (user_id, username, text, audio_url, transcript, gif_url, is_intercom, created_at, group_id, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], user["username"], None, audio_url, encrypt_text(transcript), None, is_intercom, now, group_id, parent_id),
        )
        msg_id = int(cur.lastrowid)
        recipients = get_group_user_ids(conn, group_id)

    payload = {"type": "message", "id": msg_id, "username": user["username"], "text": None, "audio_url": audio_url, "transcript": transcript, "gif_url": None, "is_intercom": is_intercom, "created_at": now, "group_id": group_id, "parent_id": parent_id}
    await manager.send_many(recipients, payload)
    send_push_to_all(user["id"], f"{user['username']} sent a voice note")
    return {"audio_url": audio_url}


def get_group_user_ids(conn: sqlite3.Connection, group_id: int) -> set[int]:
    rows = conn.execute("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,)).fetchall()
    users = {int(r["user_id"]) for r in rows}
    group = conn.execute("SELECT is_broadcast FROM groups WHERE id = ?", (group_id,)).fetchone()
    if group and int(group["is_broadcast"]) == 1:
        all_users = conn.execute("SELECT id FROM users").fetchall()
        users.update(int(u["id"]) for u in all_users)
    return users




@app.get("/api/admin/users")
def admin_list_users(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    with db() as conn:
        rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/admin/users/{target_user_id}/reset-password")
def admin_reset_password(target_user_id: int, body: PasswordResetRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pwd_context.hash(body.new_password), target_user_id))
    return {"status": "password reset"}


@app.delete("/api/admin/users/{target_user_id}")
def admin_remove_user(target_user_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    if target_user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin account")
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("DELETE FROM group_members WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM messages WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
    return {"status": "removed"}


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
    user = {"id": int(payload["sub"]), "username": payload["username"], "is_admin": bool(payload["is_admin"])}
    await manager.connect(user["id"], websocket)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            text = (data.get("text") or "").strip()
            gif_url = (data.get("gif_url") or "").strip()
            group_id = int(data.get("group_id") or 0)
            parent_id = data.get("parent_id")
            is_intercom = 1 if data.get("is_intercom") and user["is_admin"] else 0
            if not text and not gif_url or not group_id:
                continue

            with db() as conn:
                visible = get_visible_group_ids(conn, user)
                if group_id not in visible:
                    continue
                group = conn.execute("SELECT is_broadcast FROM groups WHERE id = ?", (group_id,)).fetchone()
                if group and int(group["is_broadcast"]) == 1 and not user["is_admin"]:
                    continue

                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "INSERT INTO messages (user_id, username, text, audio_url, transcript, gif_url, is_intercom, created_at, group_id, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], user["username"], encrypt_text(text) if text else None, None, None, gif_url if gif_url else None, is_intercom, now, group_id, parent_id),
                )
                msg_id = int(cur.lastrowid)
                recipients = get_group_user_ids(conn, group_id)

            payload = {"type": "message", "id": msg_id, "username": user["username"], "text": text, "audio_url": None, "transcript": None, "gif_url": gif_url if gif_url else None, "is_intercom": is_intercom, "created_at": now, "group_id": group_id, "parent_id": parent_id}
            await manager.send_many(recipients, payload)
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
        try:
            webpush(
                subscription_info={"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_SUBJECT},
            )
        except WebPushException:
            pass
