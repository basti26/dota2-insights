"""Generated atlas provenance and parser boundaries, no game resource imports."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from dota2_insights.minimap_icon_catalog import SHEET, definitions, export


def block(name='minimap_example', *, x='4', y='4', width='8', height='8', material=SHEET):
    return f'"{name}" {{ "file" "{material}" "x" "{x}" "y" "{y}" "width" "{width}" "height" "{height}" }}'


class IconCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.output = self.root / 'source', self.root / 'output'
        (self.source / 'decoded').mkdir(parents=True)
        self.definitions = self.source / 'mod_textures.txt'
        self.sheet = self.source / 'decoded/minimap_sheet_psd_b65d6dd9.png'
        self.definitions.write_text('"root" { "TextureData" {' + block() + '}}')
        Image.new('RGBA', (64, 64), (50, 100, 150, 80)).save(self.sheet)

    def test_native_rect_rgba_and_source_hashes_are_preserved(self):
        before = self.sheet.read_bytes(), self.definitions.read_bytes()
        result = export(self.source, self.output)
        entry = result['entries'][0]
        with Image.open(self.output / entry['file']) as icon:
            self.assertEqual(icon.size, (8, 8))
            self.assertEqual(icon.getpixel((0, 0)), (50, 100, 150, 80))
            self.assertEqual(hashlib.sha256(icon.tobytes()).hexdigest(), entry['rgba_pixel_sha256'])
        self.assertEqual((self.sheet.read_bytes(), self.definitions.read_bytes()), before)
        self.assertFalse(result['source_current_install_verified'])
        self.assertFalse(entry['semantic_identity_verified'])

    def test_foreign_material_is_not_followed_and_aliases_keep_separate_names(self):
        entries, bad = definitions(block('first') + block('alias') + block('external', material='../secret.vmat'), (64, 64))
        self.assertEqual({entry['name'] for entry in entries}, {'first', 'alias'})
        self.assertEqual(bad, [])

    def test_duplicate_name_field_and_escaping_name_are_refused(self):
        for value in (block() + block(), block().replace('"x" "4"', '"x" "4" "x" "5"'), block('../secret')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                definitions(value, (64, 64))

    def test_invalid_rectangles_stay_unsupported_without_guessed_coordinates(self):
        for value in (block(x='-1'), block(width='0'), block(x='60'), block().replace('"y" "4"', '')):
            entries, bad = definitions(value, (64, 64))
            self.assertEqual(entries, [])
            self.assertEqual(len(bad), 1)

    def test_missing_sheet_or_wrong_image_mode_are_refused(self):
        with self.assertRaises(ValueError):
            definitions(block(material='different.vmat'), (64, 64))
        Image.new('RGB', (64, 64)).save(self.sheet)
        with self.assertRaises(ValueError):
            export(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_source_output_overlap_and_replacing_previous_export_are_refused(self):
        with self.assertRaises(ValueError):
            export(self.source, self.source / 'must-not-write')
        export(self.source, self.output)
        before = (self.output / 'catalog.json').read_bytes()
        with self.assertRaises(FileExistsError):
            export(self.source, self.output)
        self.assertEqual((self.output / 'catalog.json').read_bytes(), before)

    def test_source_change_at_final_check_does_not_publish_catalog(self):
        import dota2_insights.minimap_icon_catalog as module
        original = module._read
        count = 0
        def changing(path, limit):
            nonlocal count
            raw = original(path, limit)
            if path == self.definitions:
                count += 1
                if count > 1:
                    return raw + b' changed'
            return raw
        with patch.object(module, '_read', changing), self.assertRaises(ValueError):
            export(self.source, self.output)
        self.assertFalse((self.output / 'catalog.json').exists())
