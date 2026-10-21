// OpenCode plugin: auto-approve read-only commands, deny dangerous ones, and ask a model about the rest.
// Falls back to asking Ryan whenever the judge is unsure or unreachable. Every decision is logged.
import { appendFileSync, readFileSync } from 'node:fs';
import { classify, JUDGE_PROMPT, parseJudge } from '/root/.config/opencode/approver-lib.mjs';

const LOG = '/var/log/opencode-approver.log';
const CONFIG = '/root/.config/opencode/opencode.json';
const WORKSPACE = '/root/homelab/';

function log(entry) {
  try { appendFileSync(LOG, JSON.stringify({ t: new Date().toISOString(), ...entry }) + '\n'); } catch {}
}

async function judge(command) {
  const provider = JSON.parse(readFileSync(CONFIG, 'utf8')).provider.local.options;
  const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 25000);
  try {
    // Use whichever model the router already has loaded, so judging never forces a model swap.
    const models = await (await fetch(provider.baseURL + '/models', { headers: { Authorization: 'Bearer ' + provider.apiKey }, signal: ctl.signal })).json();
    const loaded = (models.data || []).find(m => ['loaded', 'sleeping'].includes(m.status?.value))?.id || 'qwen3.6-35b-moe';
    const res = await fetch(provider.baseURL + '/chat/completions', {
      method: 'POST', signal: ctl.signal,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + provider.apiKey },
      body: JSON.stringify({ model: loaded, temperature: 0, max_tokens: 400, chat_template_kwargs: { enable_thinking: false },
        messages: [{ role: 'system', content: JUDGE_PROMPT }, { role: 'user', content: 'Command:\n' + command }] }),
    });
    const body = await res.json();
    return { ...parseJudge(body.choices?.[0]?.message?.content), model: loaded };
  } catch (e) {
    return { decision: 'ask', reason: 'judge unavailable: ' + (e.name || 'error') };
  } finally { clearTimeout(timer); }
}

function commandOf(p) {
  const m = p.metadata || {};
  const pat = p.patterns || p.pattern;
  return m.command || (Array.isArray(pat) ? pat.join(' ') : pat) || p.title || '';
}

async function decide(p) {
  const kind = p.permission || p.type;
  if (kind === 'edit' || kind === 'write') {
    const path = String(p.metadata?.filePath || p.metadata?.path || (p.patterns || [])[0] || p.pattern || '');
    const inside = path.startsWith(WORKSPACE) && !/CREDENTIALS\.md$/i.test(path);
    return { kind, path, decision: inside ? 'allow' : 'ask', reason: inside ? 'inside workspace' : 'outside workspace' };
  }
  if (kind !== 'bash') return { kind, decision: 'ask', reason: 'not a shell command' };
  const command = commandOf(p);
  const rule = classify(command);
  if (rule.decision !== 'judge') return { kind, command, ...rule };
  return { kind, command, ...(await judge(command)) };
}

// OpenCode 1.19 emits permission.asked and accepts a reply; the older permission.ask hook is not called.
export const HomelabApprover = async ({ client }) => ({
  event: async ({ event }) => {
    if (event?.type !== 'permission.asked') return;
    const p = event.properties || {};
    const id = p.id || p.permissionID || p.requestID;
    const sessionID = p.sessionID;
    const d = await decide(p);
    let replied = null;
    if (id && sessionID && d.decision !== 'ask') {
      const response = d.decision === 'allow' ? 'once' : 'reject';
      try {
        await client.postSessionIdPermissionsPermissionId({ path: { id: sessionID, permissionID: id }, body: { response } });
        replied = response;
      } catch (e) { replied = 'reply failed: ' + (e?.message || e); }
    }
    log({ ...d, id, replied, keys: Object.keys(p) });
  },
});
