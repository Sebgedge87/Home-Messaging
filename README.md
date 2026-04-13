# Home Messaging (Family Realtime Messenger)

A private, invite-only real-time family messaging app that runs as a **PWA (installable web app)** and can be wrapped as an APK later.

## Features

- Invite-only registration
- Real-time messaging via WebSockets
- Push notifications (Web Push + Service Worker)
- Voice notes (record and send)
- Voice-to-text (browser speech recognition)
- SQLite storage for users, invites, messages, and subscriptions
- Admin controls for invite creation

## Tech stack

- Backend: FastAPI + WebSockets + SQLite
- Frontend: Vanilla JS PWA
- Push: Service Worker + VAPID + pywebpush

## Quick start

### 1) Install deps

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Generate VAPID keys

```bash
python scripts/generate_vapid_keys.py
```

Copy output into environment variables.

### 3) Run server

```bash
export APP_SECRET_KEY='change-me'
export VAPID_PRIVATE_KEY='...'
export VAPID_PUBLIC_KEY='...'
export VAPID_CLAIMS_SUBJECT='mailto:you@example.com'
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000`

## Deploy (recommended)

Deploy to Render/Railway/Fly.io using the same env vars above.

## APK option

Use a Trusted Web Activity (TWA) or Capacitor wrapper pointed at your hosted URL to generate an APK for kids' tablets.

## Notes

- Speech-to-text depends on browser support (`SpeechRecognition` / `webkitSpeechRecognition`).
- Push notifications require HTTPS in production.
