#!/usr/bin/env node
// thousand-eyes - node wrapper: finds the installed python package and
// runs its console script. Same CLI as `pipx install thousand-eyes`.
const { spawnSync } = require('child_process');
const path = require('path');

function resolvePy() {
  const cands = ['python3', 'python'];
  if (process.env.VIRTUAL_ENV) {
    const venv = process.env.VIRTUAL_ENV;
    // windows venvs live in Scripts/, posix ones in bin/
    cands.unshift(path.join(venv, 'Scripts', 'python.exe'));
    cands.unshift(path.join(venv, 'Scripts', 'python'));
    cands.unshift(path.join(venv, 'bin', 'python'));
  }
  return cands;
}

function findScript(py) {
  const r = spawnSync(py, ['-c',
    'import thousand_eyes, os; print(os.path.dirname(thousand_eyes.__file__))'],
    { encoding: 'utf8' });
  if (r.status !== 0) return null;
  return r.stdout.trim();
}

const pys = resolvePy();
let pkgDir = null, py = null;
for (const c of pys) {
  const d = findScript(c);
  if (d) { pkgDir = d; py = c; break; }
}
if (!pkgDir) {
  console.error('thousand-eyes: python package not found.');
  console.error('install it first:  pipx install thousand-eyes   (or pip install)');
  process.exit(1);
}
const cli = path.join(pkgDir, 'cli.py');
const r = spawnSync(py, [cli, ...process.argv.slice(2)], {
  stdio: 'inherit', env: process.env,
});
process.exit(r.status ?? 1);
