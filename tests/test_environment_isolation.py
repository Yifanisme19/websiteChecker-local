import json
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.config import Settings, runtime_settings
from app.workers import WorkerPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from promote import inventory, snapshot, replace_files, DIRECTORIES, FILES, require_offline
from manage import owned_listener


class IsolationTests(unittest.TestCase):
    def test_binding_rejects_wrong_database_agent_port_and_environment(self):
        settings = Settings(_env_file=None)
        keys = ('app_env', 'api_port', 'db_host', 'db_port', 'db_name', 'db_user',
                'openclaw_agent_id', 'openclaw_base_url')
        expected = {key: getattr(settings, key) for key in keys}
        expected['openclaw_base_url'] = str(settings.openclaw_base_url).rstrip('/')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('app.config.ROOT', root), patch('app.config.Settings', return_value=settings):
                (root / '.environment.json').write_text(json.dumps(expected))
                self.assertIs(runtime_settings(), settings)
                for key in ('app_env', 'db_name', 'db_user', 'openclaw_agent_id', 'api_port'):
                    changed = dict(expected, **{key: 'wrong'})
                    (root / '.environment.json').write_text(json.dumps(changed))
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        runtime_settings()

    def test_worker_paths_are_independent(self):
        prod = WorkerPool(root='/tmp/prod')
        local = WorkerPool(root='/tmp/local')
        self.assertNotEqual(prod.socket, local.socket)
        self.assertNotEqual(prod.logs, local.logs)
        self.assertIn('directory=/tmp/local', local._program('worker-0001'))

    def test_release_excludes_configuration_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, saved = [Path(directory) / name for name in ('source', 'target', 'saved')]
            for root in (source, target):
                (root / 'app').mkdir(parents=True)
                (root / '.env').write_text('private-config')
                (root / '.environment.json').write_text('private-binding')
                (root / 'audits').mkdir()
                (root / 'audits/report.json').write_text('history')
            (source / 'app/main.py').write_text('new')
            (target / 'app/obsolete.py').write_text('old')
            candidate = inventory(source, DIRECTORIES, FILES)
            current = inventory(target, DIRECTORIES, FILES)
            self.assertEqual(set(candidate), {'app/main.py'})
            snapshot(source, saved, candidate)
            replace_files(saved, target, current, candidate)
            self.assertEqual((target / 'app/main.py').read_text(), 'new')
            self.assertFalse((target / 'app/obsolete.py').exists())
            self.assertEqual((target / '.env').read_text(), 'private-config')
            self.assertEqual((target / 'audits/report.json').read_text(), 'history')
            (source / 'app/linked.py').symlink_to(target / '.env')
            with self.assertRaises(RuntimeError):
                inventory(source, DIRECTORIES, FILES)

    def test_offline_update_and_rollback_preserve_runtime_files(self):
        from promote import main as promote_main
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            prod, local = base / 'websiteChecker', base / 'websiteChecker-local'
            prod_agent, local_agent = base / 'prod-agent', base / 'local-agent'
            for root, agent, value in ((prod, prod_agent, 'old'), (local, local_agent, 'new')):
                (root / 'app').mkdir(parents=True)
                (root / 'tests').mkdir()
                (root / 'tests/test_example.py').write_text('import unittest\nfrom pathlib import Path\nclass CandidateTests(unittest.TestCase):\n    def test_source_exists(self):\n        self.assertTrue(Path("app/main.py").is_file())\n')
                (root / 'app/main.py').write_text(value)
                (root / '.env').write_text('private-' + value)
                (root / '.environment.json').write_text(json.dumps({
                    'app_env': 'prod' if root == prod else 'local', 'agent_workspace': str(agent)}))
                (agent / 'skills/web-checker').mkdir(parents=True)
                (agent / 'skills/web-checker/SKILL.md').write_text(value)
                (agent / 'audits').mkdir()
                (agent / 'audits/report.json').write_text('keep history')
            original_binding = (prod / '.environment.json').read_text()
            with patch('promote.ROOT', prod), patch('promote.require_offline'), contextlib.redirect_stdout(io.StringIO()):
                promote_main(Settings(_env_file=None), 'update', ['--version', 'v1', '--apply'])
                self.assertEqual((prod / 'app/main.py').read_text(), 'new')
                self.assertEqual((prod_agent / 'skills/web-checker/SKILL.md').read_text(), 'new')
                self.assertEqual((prod / '.env').read_text(), 'private-old')
                self.assertEqual((prod / '.environment.json').read_text(), original_binding)
                promote_main(Settings(_env_file=None), 'rollback', ['--version', 'v1', '--apply'])
                self.assertEqual((prod / 'app/main.py').read_text(), 'old')
                self.assertEqual((prod_agent / 'skills/web-checker/SKILL.md').read_text(), 'old')
                self.assertEqual((prod_agent / 'audits/report.json').read_text(), 'keep history')

    @patch('promote.owned_listener', return_value=123)
    def test_release_rejects_live_api(self, listener):
        with self.assertRaisesRegex(RuntimeError, 'Stop Prod API'):
            require_offline(Settings(_env_file=None))

    @patch('manage.listener', return_value=123)
    @patch('manage.subprocess.check_output')
    def test_manager_cannot_stop_another_project(self, output, listener):
        output.side_effect = ['p123\nn/another/project\n', 'python -m uvicorn app.main:app']
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            owned_listener(Settings(_env_file=None))


if __name__ == '__main__':
    unittest.main()
