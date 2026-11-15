"""Exercise activity loading, including both chart creation and refresh."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(['dailyChart', 'rankingChart', 'rankingRows'].map(id => [id, {}]));
const charts = [];
const statuses = [];
let startup;
let empty = false;
let dark = false;
const context = {
  document: {
    documentElement: {},
    getElementById: id => elements.get(id),
    createElement: () => ({}),
    head: { appendChild: script => script.onload() },
  },
  getComputedStyle: () => ({ getPropertyValue: key => ({
    '--chart-1': '220 10% 50%', '--line': dark ? '#333333' : '#eeeeee',
    '--ink-faint': dark ? '#aaaaaa' : '#666666',
  })[key] }),
  Chart: function(canvas, config) {
    this.data = config.data;
    this.update = () => { this.updated = true; };
    charts.push({ canvas, config, instance: this });
  },
  AdminShell: {
    escapeHtml: String,
    byId: id => elements.get(id),
    setStatus: text => statuses.push(text),
    setMicroStatus() {}, animateNumber() {}, registerActions() {},
    bootstrapAuth: options => { startup = options.onLogin; },
    req: async url => url.includes('/overview') ? { today_messages: 3 } :
      url.includes('/daily') ? { daily: empty ? [] : [{ date: '2026-11-15', total: 3 }] } :
      { ranking: empty ? [] : [{ display_name: 'Tester', user_id: 'u1', total: 3 }] },
  },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  for (const theme of [false, true]) {
    dark = theme;
    vm.runInNewContext('dailyChartInstance = null; rankingChartInstance = null;', context);
    empty = false;
    await startup();
    const ranking = charts.at(-1).config;
    assert.equal(ranking.type, 'bar');
    assert.equal(ranking.options.scales.x.grid.color, dark ? '#333333' : '#eeeeee');
    assert.match(elements.get('rankingRows').innerHTML, /Tester/);
    assert.ok(statuses.some(text => text.includes('Tester')));
    empty = true;
    await startup();
    assert.ok(charts.at(-1).instance.updated);
    assert.equal(charts.at(-1).instance.data.labels.length, 0);
    assert.match(elements.get('rankingRows').innerHTML, /还没人冒泡/);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for admin JavaScript tests")
class AdminActivityFrontendTest(unittest.TestCase):
    def test_activity_load_and_refresh_in_both_themes(self) -> None:
        result = subprocess.run(
            ["node", "-e", SCRIPT, str(ROOT / "src/web/assets/admin/pages/activity_script.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
