let token = localStorage.getItem('token') || '';
let ws;
let mediaRecorder;
let chunks = [];
let hasUsers = true;

const authCard = document.getElementById('authCard');
const chatCard = document.getElementById('chatCard');
const messagesEl = document.getElementById('messages');
const statusEl = document.getElementById('status');
const authStatusEl = document.getElementById('authStatus');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');
const inviteInput = document.getElementById('invite');

function setStatus(t) { if (statusEl) statusEl.textContent = t; if (authStatusEl) authStatusEl.textContent = t; }
window.addEventListener('error', (e) => setStatus(`Error: ${e.message}`));
function authHeaders() { return { 'Authorization': `Bearer ${token}` }; }

async function api(path, options={}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch {}
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg || JSON.stringify(d)).join(', ') : data.detail;
    throw new Error(detail || text || `Request failed (${res.status})`);
  }
  return data;
}

function renderMessage(m) {
  const d = document.createElement('div');
  d.className = 'msg';
  let html = `<strong>${m.username}</strong> <small>${new Date(m.created_at).toLocaleString()}</small><br/>`;
  if (m.text) html += `<span>${m.text}</span>`;
  if (m.audio_url) html += `<audio controls src="${m.audio_url}"></audio>`;
  if (m.transcript) html += `<div><small>Transcript: ${m.transcript}</small></div>`;
  d.innerHTML = html;
  messagesEl.appendChild(d);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function setupPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  const reg = await navigator.serviceWorker.register('/static/sw.js');
  const cfg = await api('/api/config');
  if (!cfg.vapidPublicKey) return;

  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(cfg.vapidPublicKey)
  });
  await api('/api/subscribe', {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(sub)
  });
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map(char => char.charCodeAt(0)));
}

async function enterApp(username, isAdmin) {
  document.getElementById('welcome').textContent = `Hi ${username}`;
  authCard.style.display = 'none';
  chatCard.style.display = 'block';
  document.getElementById('inviteBtn').style.display = isAdmin ? 'inline-block' : 'none';

  const history = await api('/api/messages', { headers: authHeaders() });
  history.forEach(renderMessage);

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
  ws.onmessage = ev => renderMessage(JSON.parse(ev.data));
  ws.onopen = () => setStatus('Realtime connected');
  ws.onclose = () => setStatus('Realtime disconnected');
  await setupPush();
}

document.getElementById('loginBtn').onclick = async () => {
  try {
    const body = { username: usernameInput.value.trim(), password: passwordInput.value };
    const data = await api('/api/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    token = data.token; localStorage.setItem('token', token);
    await enterApp(data.username, data.is_admin);
  } catch (e) { setStatus(e.message); }
};

document.getElementById('registerBtn').onclick = async () => {
  try {
    if (hasUsers && !inviteInput.value.trim()) { setStatus('Invite code required (admin must create one).'); return; }
    const body = { username: usernameInput.value.trim(), password: passwordInput.value, invite_code: inviteInput.value.trim() || null };
    const data = await api('/api/register', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    token = data.token; localStorage.setItem('token', token);
    await enterApp(data.username, data.is_admin);
  } catch (e) { setStatus(e.message); }
};

document.getElementById('sendBtn').onclick = () => {
  const txt = document.getElementById('textInput').value.trim();
  if (!txt || !ws) return;
  ws.send(JSON.stringify({ text: txt }));
  document.getElementById('textInput').value = '';
};

document.getElementById('inviteBtn').onclick = async () => {
  try {
    const out = await api('/api/invites', { method:'POST', headers: authHeaders() });
    document.getElementById('inviteOut').innerText = `Invite code: ${out.code}`;
  } catch (e) { setStatus(e.message); }
};

document.getElementById('recBtn').onclick = async () => {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      const blob = new Blob(chunks, { type: 'audio/webm' });
      const form = new FormData();
      form.append('file', blob, 'voice.webm');
      form.append('transcript', document.getElementById('textInput').value || '');
      await fetch('/api/upload-audio', { method:'POST', headers: authHeaders(), body: form });
      document.getElementById('textInput').value = '';
    };
    mediaRecorder.start();
    setStatus('Recording... click again to stop');
    return;
  }

  mediaRecorder.stop();
  setStatus('Uploading voice note...');
};

document.getElementById('sttBtn').onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setStatus('Speech recognition not supported in this browser'); return; }
  const recog = new SR();
  recog.lang = 'en-US';
  recog.onresult = (e) => {
    document.getElementById('textInput').value = e.results[0][0].transcript;
    setStatus('Voice converted to text');
  };
  recog.start();
};

async function initAuthHints() {
  try {
    const data = await api('/api/bootstrap');
    hasUsers = data.has_users;
    inviteInput.placeholder = hasUsers
      ? 'Invite code required (ask admin)'
      : 'No invite needed for first account';
    if (!hasUsers) setStatus('Create the first account with Register (no invite needed).');
  } catch (e) {
    setStatus(`Backend check failed: ${e.message}`);
  }
}

initAuthHints();
