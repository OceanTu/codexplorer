import json
import tempfile
import unittest
from pathlib import Path

class ConfigurationTests(unittest.TestCase):
    def test_enable_is_idempotent_and_disable_preserves_other_edits(self):
        from codex_usage.telemetry_config import configure
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'codex';home.mkdir();data=Path(temp)/'data'
            original='model = "gpt-6-astra"\n[features]\nfoo = true\n'
            path=home/'config.toml';path.write_text(original)
            configure(home,data,9876,True);enabled=path.read_text()
            self.assertIn('127.0.0.1:9876/v1/logs',enabled)
            self.assertIn('log_user_prompt = true',enabled)
            configure(home,data,9876,True);self.assertEqual(path.read_text(),enabled)
            self.assertEqual((data/'telemetry-token').stat().st_mode & 0o777,0o600)
            path.write_text('# user edit\n'+enabled)
            configure(home,data,9876,False)
            self.assertEqual(path.read_text(),'# user edit\n'+original)

    def test_existing_exporter_is_not_overwritten(self):
        from codex_usage.telemetry_config import configure
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);data=home/'data';path=home/'config.toml'
            original='[otel]\nexporter="none"\n';path.write_text(original)
            with self.assertRaises(ValueError):configure(home,data,8765,True)
            self.assertEqual(path.read_text(),original)

    def test_modified_managed_block_is_not_removed(self):
        from codex_usage.telemetry_config import configure
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);data=home/'data';path=home/'config.toml'
            configure(home,data,8765,True)
            edited=path.read_text().replace('log_user_prompt = true','log_user_prompt = false')
            path.write_text(edited)
            with self.assertRaises(ValueError):configure(home,data,8765,False)
            self.assertEqual(path.read_text(),edited)

    def test_quoted_otel_keys_are_preserved(self):
        from codex_usage.telemetry_config import configure
        for original in ['"otel" = { exporter = "none" }\n', "'otel' = { exporter = 'none' }\n", '["otel"."exporter"]\nvalue="none"\n']:
            with self.subTest(original=original),tempfile.TemporaryDirectory() as temp:
                home=Path(temp);path=home/'config.toml';path.write_text(original)
                with self.assertRaises(ValueError):configure(home,home/'data',8765,True)
                self.assertEqual(path.read_text(),original)
