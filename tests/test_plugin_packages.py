import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from app.infrastructure.plugin_runtime import loader  # noqa: E402
from app.infrastructure.plugin_runtime import package_store as store  # noqa: E402
from app.infrastructure.runtime import PluginRuntime  # noqa: E402


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for path, content in files.items():
            z.writestr(path, content)
    return output.getvalue()


def plugin(version="one", fail=False):
    return archive({
        "demo/__init__.py": '''from domain.plugins.base import BotModule, PluginMetadata
from .helper import VALUE
class Demo(BotModule):
    @property
    def metadata(self):
        return PluginMetadata("demo", "demo", VALUE)
    async def on_load(self, handler, config=None):
        ''' + ('raise RuntimeError("broken")' if fail else 'handler.loaded.append(VALUE)') + '\n',
        "demo/helper.py": f'VALUE = "{version}"\n',
    })


class PackageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'plugins').mkdir()
        self.data = self.root / 'data'
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(store, 'DATA_DIR', str(self.data)))
        patches.enter_context(patch.object(loader, '_PROJECT_ROOT', str(self.root)))
        self.runtime = PluginRuntime(state_path=self.data / 'state.json')
        self.host = SimpleNamespace(loaded=[])
        self.addAsyncCleanup(self.runtime.stop)

    async def test_install_update_rollback_restart_and_config_preservation(self):
        result = await self.runtime.install_zip(plugin(), handler=self.host)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.runtime.discover(), ['demo'])
        self.assertTrue((self.data / 'plugins/demo/helper.py').exists())
        config = self.root / 'config/plugins/demo/config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{"custom": "preserved"}')
        self.assertFalse((await self.runtime.install_zip(plugin('two'), handler=self.host)).ok)
        result = await self.runtime.install_zip(plugin('two'), overwrite=True, handler=self.host)
        self.assertTrue(result.ok, result.message)
        loaded = self.runtime.registry.get('demo')
        assert loaded is not None
        self.assertEqual(loaded.metadata.version, 'two')
        result = await self.runtime.install_zip(plugin('bad', fail=True), overwrite=True, handler=self.host)
        self.assertFalse(result.ok)
        loaded = self.runtime.registry.get('demo')
        assert loaded is not None
        self.assertEqual(loaded.metadata.version, 'two')
        self.assertEqual(json.loads(config.read_text()), {'custom': 'preserved'})
        (self.data / 'plugins/demo/helper.py').write_text('VALUE = "new"\n')
        result = await self.runtime.reload_code('demo', handler=self.host)
        self.assertTrue(result.ok, result.message)
        loaded = self.runtime.registry.get('demo')
        assert loaded is not None
        self.assertEqual(loaded.metadata.version, 'new')
        await self.runtime.stop()
        restarted = PluginRuntime(state_path=self.data / 'state.json')
        self.addAsyncCleanup(restarted.stop)
        self.assertEqual(await restarted.load_all(handler=self.host), ['demo'])
        loaded = restarted.registry.get('demo')
        assert loaded is not None
        self.assertEqual(loaded.metadata.version, 'new')

    async def test_store_without_activation(self):
        result = await self.runtime.install_zip(plugin(), activate=False, handler=self.host)
        self.assertTrue(result.ok)
        self.assertIsNone(self.runtime.registry.get('demo'))
        self.assertEqual(self.runtime.discover(), ['demo'])
        restarted = PluginRuntime(state_path=self.data / 'state.json')
        self.assertEqual(await restarted.load_all(handler=self.host), [])

    async def test_external_discovery_does_not_require_source_plugin_directory(self):
        (self.root / 'plugins').rmdir()
        result = await self.runtime.install_zip(plugin(), activate=False, handler=self.host)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.runtime.discover(), ['demo'])

    async def test_invalid_metadata_cannot_replace_running_plugin(self):
        self.assertTrue((await self.runtime.install_zip(plugin(), handler=self.host)).ok)
        result = await self.runtime.install_zip(
            plugin('bad'), name='different', handler=self.host,
        )
        self.assertFalse(result.ok)
        self.assertIsNone(self.runtime.registry.get('different'))
        self.assertFalse((self.data / 'plugins/different').exists())
        self.assertIsNotNone(self.runtime.registry.get('demo'))

    def test_root_layout_requires_explicit_name(self):
        payload = archive({'__init__.py': '', 'helper.py': 'VALUE = 1'})
        with self.assertRaises(ValueError):
            store.stage_package(payload)
        name, stage = store.stage_package(payload, name='demo')
        self.assertEqual(name, 'demo')
        self.assertTrue((stage / '__init__.py').is_file())

    def test_bad_archives_are_rejected_without_writing_outside_storage(self):
        for files in (
            {'../escape.py': 'bad', 'demo/__init__.py': ''},
            {'/tmp/escape.py': 'bad'},
            {'demo\\escape.py': 'bad'},
            {'_shared/__init__.py': ''},
            {'demo/helper.py': ''},
            {'demo/__init__.py': 'def broken('},
            {'demo/__init__.py': '', 'other/__init__.py': ''},
        ):
            with self.subTest(files=tuple(files)), self.assertRaises(ValueError):
                store.stage_package(archive(files))
        self.assertFalse((self.root / 'escape.py').exists())
        self.assertEqual(list((self.data / 'plugins').iterdir()), [])
        with self.assertRaises(ValueError):
            store.stage_package(b'not a zip')

    def test_symlinks_and_zip_bombs_are_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            item = zipfile.ZipInfo('demo/__init__.py')
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(item, '/etc/passwd')
        with self.assertRaises(ValueError):
            store.stage_package(output.getvalue())
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('demo/__init__.py', ' ' * (2 * 1024 * 1024))
        with self.assertRaises(ValueError):
            store.stage_package(output.getvalue())
