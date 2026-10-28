// Runs every Playwright check against a local preview and prints one summary line per script.
// Usage: npm run check            the GATE scripts (must always pass); starts preview.py on 127.0.0.1:8799
//        npm run check:all        every script, including ones that fail on stale live data
//        node run-checks.mjs ui-smoke responsive-check   only these scripts
// Exit code 0 only when every script exited 0 with no FAIL line and no timeout.
import { spawn } from 'node:child_process';
import { readdirSync, mkdirSync, writeFileSync } from 'node:fs';

const URL = process.env.DASHBOARD_URL || 'http://127.0.0.1:8799/';
// Checks prefer Google Chrome; where it is not installed (e.g. the Proxmox host) they use Playwright's bundled
// Chromium instead (npx playwright install chromium-headless-shell).
import { existsSync } from 'node:fs';
const CHROME = ['/opt/google/chrome/chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:/Program Files/Google/Chrome/Application/chrome.exe'].some(p => existsSync(p));
if (!CHROME) process.env.PW_NO_CHROME = '1';
const SKIP = new Set(['memos-check', 'nightly-live-check']);  // need live services, not the preview
// Passed on the unchanged dashboard on 26 Oct 2026. The others (ai, coverage, gpu, log, log-groups) already
// failed then because they expect old live data (service counts, Kasm, GPU state); they are not a gate.
const GATE = ['accessibility-check', 'activity-check', 'android-bridge-check', 'cache-forecast-check', 'content-check', 'fonts-check', 'menu-check', 'nightly-check', 'redteam-ui-check',
  'overlay-check', 'qwen-check', 'responsive-check', 'ui-smoke', 'updates-check'];
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

// The committed index.html must be exactly what build.mjs produces from the committed sources: a bundle built from
// uncommitted or machine-specific files once deployed colour tokens the source could not reproduce (27 Oct 2026).
{
  const { execFileSync } = await import('node:child_process');
  const before = execFileSync('git', ['hash-object', 'index.html']).toString().trim();
  execFileSync('node', ['build.mjs'], { stdio: 'ignore' });
  const after = execFileSync('git', ['hash-object', 'index.html']).toString().trim();
  if (before !== after) { console.log('BAD  index.html was not built from these sources; it has now been rebuilt. Review and commit it, then rerun.'); process.exit(1); }
  console.log('OK   build                  index.html matches a fresh build');
}

let preview = null;
if (!process.env.DASHBOARD_URL) {
  // Refuse to run if something already serves the preview port: the checks would test that server's build, not this one.
  let busy = false; try { await fetch(URL); busy = true; } catch {}
  if (busy) { console.log(`BAD  ${URL} is already in use (another preview.py?). Stop it first: pkill -f "python3 preview.py"`); process.exit(1); }
  preview = spawn('python3', ['preview.py'], { stdio: 'ignore' });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(URL)).ok) break; } catch {}
    await new Promise(r => setTimeout(r, 1000));
  }
}
mkdirSync('.preview', { recursive: true });
let bad = 0; const log = [];
for (const name of scripts) {
  // Some checks read live telemetry through the preview and can flake (e.g. identical chart samples).
  // A failed script is retried once; only a script that fails twice in a row counts as BAD.
  let { code, out } = await run('node', [`${name}.mjs`], 300000);
  let retried = false;
  const failed = () => code !== 0 || /^FAIL/m.test(out) || out.includes('TIMEOUT');
  if (failed()) { retried = true; ({ code, out } = await run('node', [`${name}.mjs`], 300000)); }
  const pass = (out.match(/^PASS/gm) || []).length, fail = (out.match(/^FAIL.*$/gm) || []);
  const ok = !failed();
  if (!ok) bad++;
  console.log(`${ok ? 'OK  ' : 'BAD '} ${name.padEnd(22)} pass=${pass} fail=${fail.length} exit=${code}${out.includes('TIMEOUT') ? ' TIMEOUT' : ''}${retried ? (ok ? ' (passed on retry)' : ' (failed twice)') : ''}`);
  for (const f of fail) console.log('       ' + f.slice(0, 300));
  if (!ok && !fail.length) console.log('       ' + out.trim().split('\n').slice(-3).join(' | ').slice(0, 300));
  log.push(`### ${name}\n${out}`);
}
writeFileSync('.preview/check-output.txt', log.join('\n\n'));
if (preview) preview.kill();
console.log(bad ? `\n${bad} script(s) not OK. Full output: .preview/check-output.txt` : '\nALL CHECKS OK');
process.exit(bad ? 1 : 0);
