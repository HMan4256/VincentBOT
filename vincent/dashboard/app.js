const token = document.querySelector('meta[name="dashboard-token"]').content;
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago = ts => {
  if (!ts) return '—';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
};
let data = null, logView = 'events', memoryView = 'all';
async function request(path, options = {}) {
  const res = await fetch(path, {...options, headers: {'X-Dashboard-Token':token, ...(options.body ? {'Content-Type':'application/json'} : {})}});
  if (!res.ok) { let msg = 'Request failed'; try { msg = (await res.json()).error || msg; } catch {} throw new Error(msg); }
  return res.json();
}
function error(message) { $('#error').textContent = message; $('#error').hidden = !message; }
async function act(name, payload) {
  try { error(''); await request('/api/' + name, {method:'POST', body:JSON.stringify(payload)}); await refresh(); }
  catch (e) { error(e.message); }
}
function switchView(view) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === view));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#crumb').textContent = view.toUpperCase();
}
document.addEventListener('click', event => {
  const nav = event.target.closest('[data-view], [data-go]');
  if (nav) switchView(nav.dataset.view || nav.dataset.go);
  const log = event.target.closest('[data-log]');
  if (log) { logView = log.dataset.log; document.querySelectorAll('[data-log]').forEach(x => x.classList.toggle('active', x === log)); renderActivity(); }
  const mem = event.target.closest('[data-memory]');
  if (mem) { memoryView = mem.dataset.memory; document.querySelectorAll('[data-memory]').forEach(x => x.classList.toggle('active', x === mem)); renderMemory(); }
  const flag = event.target.closest('[data-feature]');
  if (flag) act('feature', {guild:flag.dataset.guild, channel:flag.dataset.channel, feature:flag.dataset.feature, enabled:flag.dataset.enabled !== 'true'});
  const del = event.target.closest('[data-delete-memory]');
  if (del && confirm('Delete this memory?')) act('delete-memory', {id:del.dataset.deleteMemory});
  const activate = event.target.closest('[data-activate]');
  if (activate && confirm('Activate this guidance version?')) act('activate-guidance', {id:activate.dataset.activate});
  const cancel = event.target.closest('[data-cancel]');
  if (cancel && confirm('Cancel this queue item?')) act('cancel-queue', {id:cancel.dataset.cancel});
});
function eventRow(item) {
  const label = (item.kind || 'event').replaceAll('_',' ');
  const sub = [item.channel_id ? `channel ${item.channel_id}` : '', item.author_id ? `user ${item.author_id}` : ''].filter(Boolean).join(' · ');
  return `<div class="activity-row"><div class="event-icon">↗</div><div class="row-main"><div class="row-title">${esc(label)}</div><div class="row-sub">${esc(sub || 'System event')}</div></div><div class="row-time">${esc(ago(item.occurred_at))}</div></div>`;
}
function renderOverview() {
  const status = $('#bot-status'); status.classList.toggle('offline', !data.running);
  status.innerHTML = `<span class="status-dot"></span>${data.running ? 'Bot running' : 'Bot offline'}`;
  const botAction = $('#bot-action'); botAction.hidden = data.running && !data.managed;
  botAction.textContent = data.managed ? 'Stop bot' : 'Start bot';
  const b = data.budget; $('#requests-count').textContent = `${b.local_used} / ${b.local_limit}`;
  $('#requests-foot').textContent = b.provider_blocked_until ? `Provider paused until ${new Date(b.provider_blocked_until * 1000).toLocaleTimeString()}` : 'Local requests today';
  $('#request-meter').style.width = Math.min(100, b.local_used / b.local_limit * 100) + '%';
  $('#memory-count').textContent = data.memory_total; $('#nav-memory').textContent = data.memory_total || '';
  $('#queue-count').textContent = (data.queue_counts.pending || 0) + (data.queue_counts.claimed || 0);
  $('#queue-foot').textContent = `${data.queue_counts.uncertain || 0} uncertain sends`;
  const totalVotes = data.feedback.reduce((n, r) => n + r.count, 0); $('#reaction-count').textContent = totalVotes;
  $('#reaction-foot').textContent = data.feedback.map(r => `${r.emoji} ${r.count}`).join('   ') || 'No ratings yet';
  $('#nav-events').textContent = data.events.length || ''; $('#model-name').textContent = data.model;
  $('#model-quota').textContent = b.provider_remaining === null ? 'QUOTA UNKNOWN' : `${b.provider_remaining} REMAINING · SNAPSHOT`;
  $('#recent-list').innerHTML = data.events.slice(0,6).map(eventRow).join('') || '<div class="empty">Nothing here yet. Activity will appear once Vincent starts.</div>';
  $('#channels').innerHTML = data.channels.map(c => `<div class="channel"><div class="channel-title"># Channel</div><div class="channel-id">${esc(c.channel_id)} · server ${esc(c.guild_id)}</div>${['unsolicited','reactions'].map(f => `<div class="switch-row"><span>${f === 'unsolicited' ? 'Unprompted messages' : 'Rating reactions'}</span><button class="switch ${c[f] ? 'on' : ''}" aria-label="Toggle ${esc(f)}" aria-pressed="${c[f]}" data-feature="${esc(f)}" data-guild="${esc(c.guild_id)}" data-channel="${esc(c.channel_id)}" data-enabled="${c[f]}"></button></div>`).join('')}</div>`).join('') || '<div class="empty">Channels appear after Vincent receives a message.</div>';
}
function renderActivity() {
  let html = '';
  if (logView === 'events') html = data.events.map(x => `<div class="data-row"><details><summary><div class="row-title">${esc(x.kind.replaceAll('_',' '))} <span class="row-time"> · ${esc(ago(x.occurred_at))}</span></div><div class="row-sub">${esc(x.channel_id ? 'channel ' + x.channel_id : 'system')}</div></summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details></div>`).join('');
  if (logView === 'requests') html = data.requests.map(x => `<div class="data-row"><details><summary><div class="row-title">${esc(x.kind)} request <span class="badge">${esc(x.status)}</span></div><div class="row-sub">${esc(x.model)} · ${esc(ago(x.created_at))}</div></summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details></div>`).join('');
  if (logView === 'queue') html = data.queue.map(x => `<div class="data-row"><div class="row-main"><div class="row-title">#${esc(x.id)} · ${esc(x.reason)} <span class="badge">${esc(x.status)}</span></div><div class="row-sub">user ${esc(x.author_id)} · channel ${esc(x.channel_id)} · ${esc(ago(x.created_at))}</div></div>${['pending','claimed'].includes(x.status) ? `<button class="small-button danger" data-cancel="${esc(x.id)}">Cancel</button>` : ''}</div>`).join('');
  if (logView === 'console') html = data.bot_log ? `<pre>${esc(data.bot_log)}</pre>` : '';
  $('#activity-list').innerHTML = html || '<div class="empty">No activity to show yet.</div>';
}
function renderMemory() {
  const q = $('#memory-search').value.toLowerCase();
  if (memoryView === 'profiles') {
    $('#memory-list').innerHTML = data.profiles.filter(p => JSON.stringify(p).toLowerCase().includes(q) || data.memories.some(m => m.scope === 'user' && m.user_id === p.user_id && m.guild_id === p.guild_id && m.content.toLowerCase().includes(q))).map(p => {
      const facts = data.memories.filter(m => m.scope === 'user' && m.user_id === p.user_id && m.guild_id === p.guild_id);
      return `<div class="data-row"><div class="event-icon">◉</div><details><summary><div class="row-title">${esc(p.display_name || 'Unknown user')} <span class="row-time">· ${esc(ago(p.last_interaction))}</span></div><div class="row-sub">ID ${esc(p.user_id)} · ${esc(p.incoming_count)} messages to Vincent · ${esc(p.outgoing_count)} replies · ${facts.length} memories shown · click to expand</div></summary><div class="profile-facts">${facts.length ? facts.map(m => `<div class="profile-fact"><span class="badge">${esc(m.kind)}</span> ${esc(m.content)}</div>`).join('') : '<div class="row-sub">No saved memories found in the latest 300. This profile shows interaction counts.</div>'}</div></details></div>`;
    }).join('') || '<div class="empty">No profiles found.</div>';
    return;
  }
  const profileFor = m => data.profiles.find(p => p.user_id === m.user_id && p.guild_id === m.guild_id);
  const rows = data.memories.filter(m => (memoryView === 'all' || m.scope === memoryView) && (JSON.stringify(m).toLowerCase().includes(q) || (profileFor(m)?.display_name || '').toLowerCase().includes(q)));
  $('#memory-list').innerHTML = rows.map(m => `<div class="data-row"><div class="event-icon">◈</div><div class="row-main"><div class="row-title">${esc(m.content)}</div><div class="row-sub"><span class="badge">${esc(m.scope)}</span> &nbsp;${esc(m.kind)}${m.user_id ? ` · ${esc(profileFor(m)?.display_name || 'user')} (${esc(m.user_id)})` : ''}${m.guild_id ? ` · server ${esc(m.guild_id)}` : ''} · ${esc(ago(m.updated_at))}</div></div><button class="small-button danger" data-delete-memory="${esc(m.id)}">Delete</button></div>`).join('') || '<div class="empty">No memories found.</div>';
}
function renderGuidance() {
  $('#guidance-list').innerHTML = data.versions.map(v => `<article class="panel guidance-card"><div class="panel-head"><div><div class="eyebrow">VERSION ${esc(v.id)}</div><h2>Learned guidance</h2></div><span class="badge ${v.status === 'active' ? 'active' : ''}">${esc(v.status)}</span></div><p>${esc(v.text)}</p>${v.status !== 'active' && v.status !== 'redacted' ? `<button class="small-button" data-activate="${esc(v.id)}">Activate version ↗</button>` : ''}</article>`).join('') || '<div class="panel empty">Guidance appears after the bot starts for the first time.</div>';
}
async function refresh() {
  try { data = await request('/api/data'); error(''); renderOverview(); renderActivity(); renderMemory(); renderGuidance(); $('#updated').textContent = 'Updated just now'; }
  catch (e) { error('Could not load dashboard: ' + e.message); }
}
$('#refresh').addEventListener('click', refresh);
$('#bot-action').addEventListener('click', () => { if (data.managed) { if (confirm('Stop Vincent?')) act('stop-bot', {}); } else act('start-bot', {}); });
$('#memory-search').addEventListener('input', renderMemory);
$('#add-memory').addEventListener('click', () => $('#memory-dialog').showModal());
$('#close-dialog').addEventListener('click', () => $('#memory-dialog').close());
$('#memory-scope').addEventListener('change', e => { $('#guild-field').hidden = e.target.value !== 'server'; });
$('#memory-form').addEventListener('submit', async e => { e.preventDefault(); const form = new FormData(e.target); await act('add-memory', {scope:form.get('scope'), guild:form.get('guild'), content:form.get('content')}); if ($('#error').hidden) { e.target.reset(); $('#guild-field').hidden = true; $('#memory-dialog').close(); } });
refresh(); setInterval(refresh, 15000);
