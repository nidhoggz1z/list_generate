"""Image-rule payload validation using real readers, without a Docker daemon."""
import fnmatch
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.validate_rules import validate_rules


class ImageRulesTests(unittest.TestCase):
    def test_real_rules_readable_and_hash_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / 'rules'
            rules.mkdir()
            word = next(p for p in sorted((ROOT / 'rules').glob('*材料清单.docx')) if not p.name.startswith('~$'))
            for source in (word, ROOT / 'rules/话术库对应材料.xlsx'):
                shutil.copyfile(source, rules / source.name)
            original = validate_rules(rules)
            # ZIP permits a trailing comment; this is still readable but a distinct release payload.
            with (rules / word.name).open('ab') as stream:
                stream.write(b'new-rule-version')
            self.assertNotEqual(original, validate_rules(rules))
            extra = rules / '私人文件.docx'; extra.write_bytes(b'not a release rule')
            with self.assertRaises(ValueError): validate_rules(rules)
            extra.unlink()
            (rules / '话术库对应材料.xlsx').unlink()
            with self.assertRaises(ValueError): validate_rules(rules)

    def test_invalid_word_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp)
            (rules / '坏材料清单.docx').write_bytes(b'not a word archive')
            shutil.copyfile(ROOT / 'rules/话术库对应材料.xlsx', rules / '话术库对应材料.xlsx')
            with self.assertRaises(Exception): validate_rules(rules)
            (rules / '坏材料清单.docx').unlink()
            word = next((ROOT / 'rules').glob('*材料清单.docx'))
            (rules / '链接材料清单.docx').symlink_to(word)
            with self.assertRaises(ValueError): validate_rules(rules)

    def test_build_context_and_both_dockerfiles_include_only_release_rules(self):
        ignore = (ROOT / '.dockerignore').read_text()
        self.assertTrue(ignore.isascii(), 'BuildKit exclude-patterns metadata must be printable ASCII')
        patterns = ignore.splitlines()
        def included(name):
            keep = False
            for pattern in patterns:
                allow = pattern.startswith('!')
                pattern = pattern.lstrip('!')
                if fnmatch.fnmatch(name, pattern): keep = allow
            return keep
        self.assertTrue(included('rules/产品材料清单.docx'))
        self.assertTrue(included('rules/话术库对应材料.xlsx'))
        self.assertFalse(included('rules/~$产品材料清单.docx'))
        self.assertFalse(included('rules/私人文件.txt'))
        self.assertFalse(included('rules/private.env'))
        for name in ('Dockerfile', 'Dockerfile.bank'):
            text = (ROOT / name).read_text()
            self.assertIn('COPY rules/ ./rules/', text)
            self.assertIn('-B -m service.validate_rules', text)
        bank = (ROOT / 'Dockerfile.bank').read_text()
        self.assertLess(bank.index('USER appuser:0'), bank.index('RUN /app/.venv/bin/python -B -m service.validate_rules'))
        self.assertIn('CMD ["/app/.venv/bin/python",', bank)


if __name__ == '__main__': unittest.main(verbosity=2)
