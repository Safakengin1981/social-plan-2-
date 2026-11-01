import unittest, tempfile, sqlite3, pathlib, os
from unittest.mock import patch
import backup_full
class BackupTests(unittest.TestCase):
 def test_database_and_media_are_included(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td); db=base/'db.sqlite3'; media=base/'uploads'; (media/'1').mkdir(parents=True)
   (media/'1'/'sample.jpg').write_bytes(b'example')
   with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE sample (id INTEGER)');conn.execute('INSERT INTO sample VALUES (1)')
   with patch.object(backup_full,'DB',db),patch.object(backup_full,'MEDIA',media),patch.object(backup_full,'OUT',base/'backups'):
    output=backup_full.create_backup()
   import zipfile
   with zipfile.ZipFile(output) as z:
    self.assertIn('database/socialplan.sqlite3',z.namelist())
    self.assertEqual(z.read('uploads/1/sample.jpg'),b'example')
    self.assertIn('manifest.json',z.namelist())
