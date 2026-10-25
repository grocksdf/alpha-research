"""Release inventory and credential-pattern scanner behavior; no real secrets."""
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

from scripts.release import scan_text,refresh_manifest,verify_sources,create_archive,verify_archive


class ReleaseTests(TestCase):
    def test_known_credential_patterns_are_rejected_without_echoing_values(self):
        secrets=['sk-'+'a'*30,'ghp_'+'b'*36,'AKIA'+'C'*16,'BRAIN_PASSWORD = '+repr('fixture-password')]
        for value in secrets:
            findings=scan_text('test.py',value)
            self.assertTrue(findings);self.assertNotIn(value,repr(findings))

    def test_environment_reads_and_empty_config_placeholders_are_allowed(self):
        self.assertEqual(scan_text('test.py','password = os.getenv("BRAIN_PASSWORD")\nLLM_API_KEY = ""'),[])

    def test_archive_excludes_runtime_and_live_config_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'README.md').write_text('TEST release',encoding='utf-8')
            (root/'state').mkdir();(root/'state/live.sqlite3').write_bytes(b'PRIVATE')
            (root/'config').mkdir();(root/'config/live.toml').write_text('PRIVATE',encoding='utf-8')
            refresh_manifest(root);self.assertEqual(verify_sources(root),1)
            a=root/'one.zip';b=root/'two.zip'
            self.assertEqual(create_archive(a,root),create_archive(b,root))
            self.assertEqual(verify_archive(a),1)
            with zipfile.ZipFile(a) as z:self.assertEqual(len(z.namelist()),2)

    def test_tampered_archive_or_source_fails_integrity_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'README.md').write_text('TEST',encoding='utf-8');refresh_manifest(root)
            archive=root/'release.zip';create_archive(archive,root)
            (root/'README.md').write_text('changed',encoding='utf-8')
            with self.assertRaises(ValueError):verify_sources(root)
            with zipfile.ZipFile(archive,'a') as z:z.writestr('alpha-research/EXTRA.txt','unexpected')
            with self.assertRaises(ValueError):verify_archive(archive)

    def test_benchmark_is_mandatory_for_source_and_archives(self):
        from scripts.release import inventory
        root=Path(__file__).resolve().parents[1]
        self.assertIn(Path('benchmarks/evaluate_memory.py'),inventory(root))
        self.assertEqual(verify_sources(root),len(inventory(root)))
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'fixed-source.zip'
            create_archive(archive,root)
            with zipfile.ZipFile(archive) as source:
                self.assertIn('alpha-research/benchmarks/evaluate_memory.py',source.namelist())
                self.assertIn('alpha-research/benchmarks/__init__.py',source.namelist())
                self.assertIn('alpha-research/tests/test_v08_benchmark.py',source.namelist())
            self.assertEqual(verify_archive(archive),len(inventory(root)))

    def test_configured_research_rules_must_be_in_release_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir()
            (root/'config/settings.toml').write_text('program = "program.md"\n',encoding='utf-8')
            refresh_manifest(root)
            with self.assertRaisesRegex(ValueError,'配置依赖'):verify_sources(root)
            (root/'program.md').write_text('TEST frozen research rules',encoding='utf-8')
            refresh_manifest(root);self.assertEqual(verify_sources(root),2)
