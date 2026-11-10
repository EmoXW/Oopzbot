"""Run the real admin shell in Node with a minimal DOM and HTTP boundary."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "src/web/assets/admin/admin-shell.js"

SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scenario = process.argv[2];
const elements = new Map();
for (const id of ['loginCard', 'panel', 'pwd', 'loginMsg', 'ctlMsg', 'topStatus', 'mobileStatus']) {
  const classes = new Set(id === 'panel' ? ['hidden'] : []);
  elements.set(id, {
    dataset: {}, textContent: '', value: '', className: '', addEventListener() {},
    classList: {
      add(value) { classes.add(value); },
      toggle(value, on) { if (on) classes.add(value); else classes.delete(value); },
      contains(value) { return classes.has(value); },
    },
  });
}
let authorized = false;
let loggedOut = 0;
let loginErrors = 0;
let loads = 0;
const context = {
  window: { addEventListener() {} },
  document: { getElementById: id => elements.get(id) || null, addEventListener() {} },
  requestAnimationFrame() {},
  fetch: async (url, options) => {
    if (url === '/admin/api/login') {
      const valid = JSON.parse(options.body).password === 'test-password';
      authorized = valid;
      return { ok: valid, status: valid ? 200 : 403,
        json: async () => valid ? { ok: true } : { ok: false, error: '密码错误' } };
    }
    assert.equal(url, '/admin/api/me');
    return { ok: authorized, status: authorized ? 200 : 401,
      json: async () => ({ ok: authorized, error: '后台未登录' }) };
  },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  const auth = context.window.AdminShell.bootstrapAuth({
    page: 'music', loadMsgId: 'ctlMsg',
    onLogin: async () => {
      loads++;
      if (scenario === 'load_failure') throw new Error('当前没有可用的播放域');
    },
    onLoggedOut: () => loggedOut++,
    onLoginError: () => loginErrors++,
  });
  await new Promise(resolve => setImmediate(resolve));
  elements.get('pwd').value = scenario === 'bad_password' ? 'wrong' : 'test-password';
  await auth.login();
  if (scenario === 'bad_password') {
    assert.equal(elements.get('panel').classList.contains('hidden'), true);
    assert.equal(elements.get('loginCard').classList.contains('hidden'), false);
    assert.equal(elements.get('loginMsg').textContent, '密码错误');
    assert.equal(loginErrors, 1);
    assert.equal(loads, 0);
  } else {
    assert.equal(elements.get('panel').classList.contains('hidden'), false);
    assert.equal(elements.get('loginCard').classList.contains('hidden'), true);
    assert.equal(loggedOut, 1);
    assert.equal(loginErrors, 0);
    if (scenario === 'load_failure') {
      assert.equal(elements.get('ctlMsg').textContent, '当前没有可用的播放域');
      assert.match(elements.get('topStatus').textContent, /当前没有可用的播放域/);
      await auth.check();
      assert.equal(elements.get('panel').classList.contains('hidden'), false);
      assert.equal(loggedOut, 1);
    } else if (scenario === 'expired') {
      authorized = false;
      await auth.check();
      assert.equal(elements.get('panel').classList.contains('hidden'), true);
      assert.equal(elements.get('loginCard').classList.contains('hidden'), false);
      assert.equal(loggedOut, 2);
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for admin JavaScript tests")
class AdminAuthFrontendTest(unittest.TestCase):
    def test_authentication_and_page_loading_have_independent_outcomes(self) -> None:
        for scenario in ("load_failure", "bad_password", "success", "expired"):
            with self.subTest(scenario=scenario):
                result = subprocess.run(
                    ["node", "-e", SCRIPT, str(SHELL), scenario],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=15,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
