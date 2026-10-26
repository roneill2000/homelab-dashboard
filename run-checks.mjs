// Runs every Playwright check against a local preview and prints one summary line per script.
// Usage: npm run check            the GATE scripts (must always pass); starts preview.py on 127.0.0.1:8799
//        npm run check:all        every script, including ones that fail on stale live data
//        node run-checks.mjs ui-smoke responsive-check   only these scripts
// Exit code 0 only when every script exited 0 with no FAIL line and no timeout.
import { spawn } from 'node:child_process';
import { readdirSync, mkdirSync, writeFileSync } from 'node:fs';

const URL = process.env.DASHBOARD_URL || 'http://127.0.0.1:8799/';
const SKIP = new Set(['memos-check', 'nightly-live-check']);  // need live services, not the preview
// Passed on the unchanged dashboard on 26 Oct 2026. The others (ai, coverage, gpu, log, log-groups) already
// failed then because they expect old live data (service counts, Kasm, GPU state); they are not a gate.
const GATE = ['accessibility-check', 'activity-check', 'cache-forecast-check', 'content-check', 'nightly-check',
  'qwen-check', 'responsive-check', 'ui-smoke', 'updates-check'];
const args = process.argv.slice(2), all = args.includes('--all');
const wanted = args.filter(a => a !== '--all');
if (!all && !wanted.length) wanted.push(...GATE);
const scripts = readdirSync('.').filter(f => /^(ui-smoke|.*-check)\.mjs$/.test(f)).map(f => f.slice(0, -4))
  .filter(n => !SKIP.has(n) && (!wanted.length || wanted.includes(n))).sort();

const run = (cmd, args, ms) => new Promise(resolve => {
  const p = spawn(cmd, args, { env: { ...process.env, DASHBOARD_URL: URL } });
  let out = ''; p.stdout.on('data', d => out += d); p.stderr.on('data', d => out += d);
  const t = setTimeout(() => { p.kill('SIGKILL'); out += '\nTIMEOUT'; }, ms);
  p.on('close', code => { clearTimeout(t); resolve({ code, out }); });
});

let preview = null;
if (!process.env.DASHBOARD_URL) {
  preview = spawn('python3', ['preview.py'], { stdio: 'ignore' });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(URL)).ok) break; } catch {}
    await new Promise(r => setTimeout(r, 1000));
  }
}
mkdirSync('.preview', { recursive: true });
let bad = 0; const log = [];
for (const name of scripts) {
  const { code, out } = await run('node', [`${name}.mjs`], 300000);
  const pass = (out.match(/^PASS/gm) || []).length, fail = (out.match(/^FAIL.*$/gm) || []);
  const ok = code === 0 && fail.length === 0 && !out.includes('TIMEOUT');
  if (!ok) bad++;
  console.log(`${ok ? 'OK  ' : 'BAD '} ${name.padEnd(22)} pass=${pass} fail=${fail.length} exit=${code}${out.includes('TIMEOUT') ? ' TIMEOUT' : ''}`);
  for (const f of fail) console.log('       ' + f.slice(0, 300));
  if (!ok && !fail.length) console.log('       ' + out.trim().split('\n').slice(-3).join(' | ').slice(0, 300));
  log.push(`### ${name}\n${out}`);
}
writeFileSync('.preview/check-output.txt', log.join('\n\n'));
if (preview) preview.kill();
console.log(bad ? `\n${bad} script(s) not OK. Full output: .preview/check-output.txt` : '\nALL CHECKS OK');
process.exit(bad ? 1 : 0);
