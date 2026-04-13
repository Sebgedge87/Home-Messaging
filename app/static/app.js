let token = localStorage.getItem('token') || '';
let ws;
let mediaRecorder;
let chunks = [];
let hasUsers = true;
let isAdmin = false;
let currentGroupId = null;
let replyToId = null;

let myUsername = localStorage.getItem('username') || '';
let myThemeColor = '#5b8cff';
let myWallpaper = '';

function applySettings(color, wp, fontFam, fontSize, themeBg, themeText, themeTheirs) {
  document.documentElement.style.setProperty('--user-theme', color);
  document.documentElement.style.setProperty('--user-font', fontFam || 'Inter');
  document.documentElement.style.setProperty('--user-font-size', fontSize || '15px');
  document.documentElement.style.setProperty('--bg', themeBg || '#09090b');
  document.documentElement.style.setProperty('--text', themeText || '#f4f4f5');
  document.documentElement.style.setProperty('--theirs-bg', themeTheirs || '#18181b');
  
  const themeInput = document.getElementById('themeInput');
  const bgInput = document.getElementById('bgInput');
  const textInputColor = document.getElementById('textInputColor');
  const theirsInput = document.getElementById('theirsInput');
  const wallpaperInput = document.getElementById('wallpaperInput');
  const fontFamilyInput = document.getElementById('fontFamilyInput');
  const fontSzInput = document.getElementById('fontSizeInput');
  const fontSizeDisplay = document.getElementById('fontSizeDisplay');

  if (themeInput) themeInput.value = color;
  if (bgInput) bgInput.value = themeBg || '#09090b';
  if (textInputColor) textInputColor.value = themeText || '#f4f4f5';
  if (theirsInput) theirsInput.value = themeTheirs || '#18181b';
  if (wallpaperInput) wallpaperInput.value = wp || '';
  if (fontFamilyInput) fontFamilyInput.value = fontFam || 'Inter';
  if (fontSzInput) fontSzInput.value = parseInt(fontSize) || 15;
  if (fontSizeDisplay) fontSizeDisplay.innerText = `${parseInt(fontSize) || 15}px`;

  document.body.style.fontFamily = `var(--user-font), ui-sans-serif, system-ui, sans-serif`;
  document.body.style.fontSize = `var(--user-font-size)`;

  if (wp) {
    document.body.style.backgroundImage = `url(${wp})`;
    document.body.style.backgroundSize = 'cover';
    document.body.style.backgroundPosition = 'center';
    document.body.style.backgroundAttachment = 'fixed';
  } else {
    document.body.style.backgroundImage = 'none';
  }
}

const authCard = document.getElementById('authCard');
const chatCard = document.getElementById('chatCard');
const settingsCard = document.getElementById('settingsCard');
const navButtons = document.getElementById('navButtons');
const navChatBtn = document.getElementById('navChatBtn');
const navSettingsBtn = document.getElementById('navSettingsBtn');

if (navSettingsBtn && navChatBtn) {
  navSettingsBtn.onclick = () => {
    chatCard.style.display = 'none';
    settingsCard.style.display = 'block';
    navSettingsBtn.style.display = 'none';
    navChatBtn.style.display = 'block';
  };

  navChatBtn.onclick = () => {
    settingsCard.style.display = 'none';
    chatCard.style.display = 'block';
    navChatBtn.style.display = 'none';
    navSettingsBtn.style.display = 'block';
  };
}

function showModal({ title, message, hasInput, isConfirm }) {
  return new Promise(resolve => {
    const overlay = document.getElementById('modalOverlay');
    const titleEl = document.getElementById('modalTitle');
    const msgEl = document.getElementById('modalMessage');
    const inputEl = document.getElementById('modalInput');
    const okBtn = document.getElementById('modalOk');
    const cancelBtn = document.getElementById('modalCancel');

    titleEl.textContent = title;
    msgEl.textContent = message || '';
    inputEl.style.display = hasInput ? 'block' : 'none';
    inputEl.value = '';
    
    if (isConfirm) {
      okBtn.textContent = 'Yes';
      cancelBtn.style.display = 'block';
    } else if (hasInput) {
      okBtn.textContent = 'OK';
      cancelBtn.style.display = 'block';
    } else {
      okBtn.textContent = 'OK';
      cancelBtn.style.display = 'none';
    }

    overlay.style.display = 'flex';
    if (hasInput) inputEl.focus();

    const cleanup = () => {
      overlay.style.display = 'none';
      okBtn.onclick = null;
      cancelBtn.onclick = null;
    };

    okBtn.onclick = () => {
      cleanup();
      resolve(hasInput ? inputEl.value.trim() : true);
    };

    cancelBtn.onclick = () => {
      cleanup();
      resolve(hasInput ? null : false);
    };
  });
}

const messagesEl = document.getElementById('messages');
const statusEl = document.getElementById('status');
const authStatusEl = document.getElementById('authStatus');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');
const inviteInput = document.getElementById('invite');
const ownerKeyInput = document.getElementById('ownerKey');
const groupsEl = document.getElementById('groupsList');
const contactsListEl = document.getElementById('contactsList');
const groupTitleEl = document.getElementById('groupTitle');
const replyInfoEl = document.getElementById('replyInfo');
const adminPanelEl = document.getElementById('adminPanel');
const membersListEl = document.getElementById('membersList');
const rememberMeEl = document.getElementById('rememberMe');

function setStatus(t) { if (statusEl) statusEl.textContent = t; if (authStatusEl) authStatusEl.textContent = t; }
function loadRememberedDetails() {
  const u = localStorage.getItem('saved_username');
  const p = localStorage.getItem('saved_password');
  if (u) usernameInput.value = u;
  if (p) passwordInput.value = p;
}
function saveRememberedDetails() {
  if (!rememberMeEl?.checked) return;
  localStorage.setItem('saved_username', usernameInput.value.trim().toLowerCase());
  localStorage.setItem('saved_password', passwordInput.value);
}
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

function renderGroups(groups) {
  groupsEl.innerHTML = '';
  groups.forEach(g => {
    const b = document.createElement('button');
    b.textContent = g.name + (g.is_broadcast ? ' 📢' : '');
    b.style.width = '100%';
    b.style.marginBottom = '6px';
    b.onclick = async () => {
      currentGroupId = g.id;
      groupTitleEl.textContent = `Conversation: ${g.name}`;
      await loadMessages();
    };
    groupsEl.appendChild(b);
  });
}

async function loadContacts() {
  try {
    const contacts = await api('/api/contacts', { headers: authHeaders() });
    renderContacts(contacts);
  } catch (e) {
    if (statusEl) setStatus(`Failed to load contacts: ${e.message}`);
  }
}

function renderContacts(contacts) {
  if (!contactsListEl) return;
  contactsListEl.innerHTML = '';
  if (contacts.length === 0) {
    contactsListEl.innerHTML = '<small>No contacts yet</small>';
    return;
  }
  contacts.forEach(c => {
    const b = document.createElement('button');
    b.textContent = '👤 ' + c.username;
    b.style.width = '100%';
    b.style.marginBottom = '6px';
    b.style.background = '#1e293b';
    b.onclick = async () => {
      try {
        const res = await api(`/api/direct/${c.id}`, { method: 'POST', headers: authHeaders() });
        const groups = await api('/api/groups', { headers: authHeaders() });
        renderGroups(groups);
        currentGroupId = res.id;
        const groupObj = groups.find(g => g.id === currentGroupId);
        groupTitleEl.textContent = `Conversation: ${groupObj ? groupObj.name : 'Direct'}`;
        await loadMessages();
      } catch(e) {
        setStatus(`Failed to start direct message: ${e.message}`);
      }
    };
    contactsListEl.appendChild(b);
  });
}

async function loadMembers() {
  if (!isAdmin) return;
  const users = await api('/api/admin/users', { headers: authHeaders() });
  renderMembers(users);
}

function renderMembers(users) {
  membersListEl.innerHTML = '';
  users.forEach(u => {
    const row = document.createElement('div');
    row.className = 'panel';
    row.style.marginBottom = '6px';
    row.innerHTML = `<div><strong>${u.username}</strong>${u.is_admin ? ' (admin)' : ''}</div>`;

    if (!u.is_admin) {
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'Reset password';
      resetBtn.style.width = '100%';
      resetBtn.style.marginTop = '6px';
      resetBtn.onclick = async () => {
        const np = await showModal({ title: 'Reset password', message: `New password for ${u.username}`, hasInput: true });
        if (!np) return;
        await api(`/api/admin/users/${u.id}/reset-password`, { method:'POST', headers:{'Content-Type':'application/json', ...authHeaders()}, body: JSON.stringify({ new_password: np }) });
        setStatus(`Password reset for ${u.username}`);
      };

      const removeBtn = document.createElement('button');
      removeBtn.textContent = 'Remove user';
      removeBtn.style.width = '100%';
      removeBtn.style.marginTop = '6px';
      removeBtn.onclick = async () => {
        const confirmed = await showModal({ title: 'Remove user', message: `Remove ${u.username}?`, isConfirm: true });
        if (!confirmed) return;
        await api(`/api/admin/users/${u.id}`, { method:'DELETE', headers: authHeaders() });
        setStatus(`${u.username} removed`);
        await loadMembers();
      };
      row.appendChild(resetBtn);
      row.appendChild(removeBtn);
    }
    membersListEl.appendChild(row);
  });

}

function renderMessage(m) {
  if (m.group_id !== currentGroupId) return;
  const d = document.createElement('div');
  d.className = 'msg ' + (m.username === myUsername ? 'msg-mine' : 'msg-theirs');
  if (m.parent_id) d.style.marginLeft = '20px';
  let html = `<strong>${m.username}</strong> <small>${new Date(m.created_at).toLocaleString()}</small>`;
  if (m.parent_id) html += ` <small>↪ reply to #${m.parent_id}</small>`;
  html += '<br/>';
  if (m.text) html += `<span>${m.text}</span>`;
  if (m.audio_url) html += `<audio controls src="${m.audio_url}"></audio>`;
  if (m.transcript) html += `<div><small>Transcript: ${m.transcript}</small></div>`;
  html += `<div><button data-reply="${m.id || ''}">Reply</button></div>`;
  d.innerHTML = html;
  d.querySelector('button')?.addEventListener('click', () => {
    replyToId = m.id || null;
    replyInfoEl.textContent = replyToId ? `Replying to #${replyToId}` : '';
  });
  messagesEl.appendChild(d);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function loadMessages() {
  if (!currentGroupId) return;
  messagesEl.innerHTML = '';
  const history = await api(`/api/messages?group_id=${currentGroupId}`, { headers: authHeaders() });
  history.forEach(renderMessage);
}

async function setupPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  const reg = await navigator.serviceWorker.register('/static/sw.js');
  const cfg = await api('/api/config');
  if (!cfg.vapidPublicKey) return;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(cfg.vapidPublicKey) });
  await api('/api/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(sub) });
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from([...atob(base64)].map(char => char.charCodeAt(0)));
}

async function openSocket() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
  ws.onmessage = ev => renderMessage(JSON.parse(ev.data));
  ws.onopen = () => setStatus('Realtime connected');
  ws.onclose = () => setStatus('Realtime disconnected');
}

async function enterApp(username, adminFlag, theme, wallpaper, fontFamily, fontSize, themeBg, themeText, themeTheirs) {
  myUsername = username;
  isAdmin = adminFlag;
  myThemeColor = theme || '#ffffff';
  myWallpaper = wallpaper || '';
  applySettings(myThemeColor, myWallpaper, fontFamily, fontSize, themeBg, themeText, themeTheirs);

  document.getElementById('welcome').textContent = `Hi ${username}`;
  authCard.style.display = 'none';
  chatCard.style.display = 'block';
  navButtons.style.display = 'flex';
  document.getElementById('inviteBtn').style.display = isAdmin ? 'inline-block' : 'none';
  document.getElementById('createGroupBtn').style.display = isAdmin ? 'inline-block' : 'none';
  adminPanelEl.style.display = isAdmin ? 'block' : 'none';

  const groups = await api('/api/groups', { headers: authHeaders() });
  renderGroups(groups);
  await loadContacts();
  currentGroupId = groups[0]?.id || null;
  groupTitleEl.textContent = currentGroupId ? `Conversation: ${groups[0].name}` : 'No groups yet';
  await loadMessages();
  await openSocket();
  await setupPush();
  if (isAdmin) await loadMembers();
}

document.getElementById('loginBtn').onclick = async () => {
  try {
    const body = { username: usernameInput.value.trim().toLowerCase(), password: passwordInput.value };
    const data = await api('/api/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    token = data.token; localStorage.setItem('token', token); localStorage.setItem('username', data.username); saveRememberedDetails();
    await enterApp(data.username, data.is_admin, data.theme_color, data.wallpaper_url, data.font_family, data.font_size, data.theme_bg, data.theme_text, data.theme_theirs);
  } catch (e) { setStatus(e.message); }
};

document.getElementById('registerBtn').onclick = async () => {
  try {
    const ownerKey = ownerKeyInput.value.trim();
    if (hasUsers && !inviteInput.value.trim() && !ownerKey) { setStatus('Invite code required (or owner setup key).'); return; }
    const body = { username: usernameInput.value.trim().toLowerCase(), password: passwordInput.value, invite_code: inviteInput.value.trim() || null, owner_key: ownerKey || null };
    const data = await api('/api/register', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    token = data.token; localStorage.setItem('token', token); localStorage.setItem('username', data.username); saveRememberedDetails();
    await enterApp(data.username, data.is_admin, data.theme_color, data.wallpaper_url, data.font_family, data.font_size, data.theme_bg, data.theme_text, data.theme_theirs);
  } catch (e) { setStatus(e.message); }
};

const emojiBtn = document.getElementById('emojiBtn');
const emojiPicker = document.getElementById('emojiPicker');
const textInput = document.getElementById('textInput');

if (emojiBtn && emojiPicker) {
  emojiBtn.addEventListener('click', () => {
    emojiPicker.style.display = emojiPicker.style.display === 'none' ? 'block' : 'none';
  });
  emojiPicker.addEventListener('emoji-click', event => {
    textInput.value += event.detail.unicode;
    emojiPicker.style.display = 'none';
    textInput.focus();
  });
  document.addEventListener('click', e => {
    if (!emojiPicker.contains(e.target) && e.target !== emojiBtn) {
      emojiPicker.style.display = 'none';
    }
  });
}

document.getElementById('saveThemeBtn').onclick = async () => {
  const c = document.getElementById('themeInput').value;
  const b = document.getElementById('bgInput').value;
  const t = document.getElementById('textInputColor').value;
  const th = document.getElementById('theirsInput').value;
  const w = document.getElementById('wallpaperInput').value.trim();
  const f = document.getElementById('fontFamilyInput').value;
  const fs = document.getElementById('fontSizeInput').value + 'px';
  try {
    await api('/api/settings', { method:'POST', headers:{'Content-Type':'application/json', ...authHeaders()}, body: JSON.stringify({
      theme_color: c, 
      wallpaper_url: w || null, 
      font_family: f, 
      font_size: fs,
      theme_bg: b,
      theme_text: t,
      theme_theirs: th
    })});
    applySettings(c, w, f, fs, b, t, th);
    setStatus('Settings saved');
  } catch(e) { setStatus(e.message); }
};

const fontSlider = document.getElementById('fontSizeInput');
const fontDisplay = document.getElementById('fontSizeDisplay');
if (fontSlider && fontDisplay) {
  fontSlider.addEventListener('input', (e) => {
    fontDisplay.innerText = `${e.target.value}px`;
  });
}

textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    document.getElementById('sendBtn').click();
  }
});

document.getElementById('sendBtn').onclick = () => {
  const txt = textInput.value.trim();
  if (!txt || !ws || !currentGroupId) return;
  ws.send(JSON.stringify({ text: txt, group_id: currentGroupId, parent_id: replyToId }));
  document.getElementById('textInput').value = '';
  replyToId = null;
  replyInfoEl.textContent = '';
};

document.getElementById('inviteBtn').onclick = async () => {
  try {
    const out = await api('/api/invites', { method:'POST', headers: authHeaders() });
    document.getElementById('inviteOut').innerText = `Invite code: ${out.code}`;
  } catch (e) { setStatus(e.message); }
};

document.getElementById('createGroupBtn').onclick = async () => {
  const name = await showModal({ title: 'New Group', message: 'Group name:', hasInput: true });
  if (!name) return;
  const isBroadcast = await showModal({ title: 'Broadcast group?', message: 'Only admins should post?', isConfirm: true });
  try {
    await api('/api/groups', { method:'POST', headers:{'Content-Type':'application/json', ...authHeaders()}, body: JSON.stringify({ name, is_broadcast: isBroadcast })});
    const groups = await api('/api/groups', { headers: authHeaders() });
    renderGroups(groups);
  } catch (e) { setStatus(e.message); }
};

document.getElementById('recBtn').onclick = async () => {
  if (!currentGroupId) { setStatus('Choose a group first'); return; }
  if (!mediaRecorder || mediaRecorder.state === 'inactive') {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      const blob = new Blob(chunks, { type: 'audio/webm' });
      const form = new FormData();
      form.append('file', blob, 'voice.webm');
      form.append('group_id', String(currentGroupId));
      form.append('transcript', document.getElementById('textInput').value || '');
      if (replyToId) form.append('parent_id', String(replyToId));
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

let recog = null;
document.getElementById('sttBtn').onclick = () => {
  if (recog) {
    recog.stop();
    recog = null;
    setStatus('Voice-to-text stopped');
    return;
  }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setStatus('Voice-to-text not supported on this browser. Use Chrome/Edge over HTTPS.'); return; }
  recog = new SR();
  recog.lang = 'en-US';
  setStatus('Listening...');
  recog.onresult = (e) => { 
    document.getElementById('textInput').value = e.results[0][0].transcript; 
    setStatus('Voice converted to text'); 
    recog = null; 
  };
  recog.onerror = (e) => { 
    if (e.error === 'aborted' || e.error === 'no-speech') {
      setStatus('Listening stopped');
    } else {
      setStatus(`Voice-to-text error: ${e.error}`); 
    }
    recog = null;
  };
  recog.onend = () => { recog = null; };
  recog.start();
};

document.getElementById('logoutBtn').onclick = () => {
  localStorage.removeItem('token');
  token = '';
  location.reload();
};

async function initAuthHints() {
  try {
    const data = await api('/api/bootstrap');
    hasUsers = data.has_users;
    inviteInput.placeholder = hasUsers ? 'Invite code required (ask admin), or use owner setup key' : 'No invite needed for first account';
    if (!hasUsers) setStatus('Create the first account with Register (no invite needed).');
  } catch (e) {
    setStatus(`Backend check failed: ${e.message}`);
  }
}

async function autoLogin() {
  if (!token) return;
  try {
    const me = await api('/api/me', { headers: authHeaders() });
    await enterApp(me.username, me.is_admin, me.theme_color, me.wallpaper_url, me.font_family, me.font_size, me.theme_bg, me.theme_text, me.theme_theirs);
  } catch {
    localStorage.removeItem('token');
  }
}

loadRememberedDetails();
initAuthHints();
autoLogin();
