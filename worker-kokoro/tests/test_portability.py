import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instance_lock import InstanceLock, WorkerAlreadyRunning
from runtime_config import WorkerPaths
from sqlite_maintenance import integrity_check, backup_database, restore_database


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_worker_paths_are_external_and_distinct(self):
        with patch.dict(os.environ, {"TTS_OUTPUT_DIR": str(self.root / "state"),
                                     "TTS_MODEL_DATA_DIR": str(self.root / "weights")}):
            paths = WorkerPaths.from_environment(self.root / "code").prepare()
            self.assertEqual(self.root / "state", paths.output)
            self.assertEqual(self.root / "weights" / "models", paths.models)
        with patch.dict(os.environ, {"TTS_OUTPUT_DIR": str(self.root),
                                     "TTS_MODEL_DATA_DIR": str(self.root)}):
            with self.assertRaises(ValueError):
                WorkerPaths.from_environment(self.root)

    def test_single_instance_lock_denies_second_owner(self):
        with InstanceLock(self.root):
            with self.assertRaises(WorkerAlreadyRunning):
                InstanceLock(self.root).acquire()
        with InstanceLock(self.root):
            pass

    def test_lock_is_released_by_hard_process_exit(self):
        script = ("import sys; from instance_lock import InstanceLock; "
                  "InstanceLock(sys.argv[1]).acquire(); print('locked',flush=True); "
                  "import os; os._exit(42)")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        proc = subprocess.run([sys.executable, "-c", script, str(self.root)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=10)
        self.assertEqual(42, proc.returncode)
        self.assertIn(b"locked", proc.stdout)
        with InstanceLock(self.root):
            pass

    def test_online_backup_and_offline_restore(self):
        db = self.root / "db.sqlite"
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE work(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO work VALUES(1,'durable')")
        copy = backup_database(db, self.root / "backups" / "db.sqlite")
        self.assertEqual("ok", integrity_check(copy))
        with self.assertRaises(ValueError):
            restore_database(copy, self.root / "restored.sqlite")
        restored = restore_database(copy, self.root / "restored.sqlite", stopped=True)
        with sqlite3.connect(restored) as conn:
            self.assertEqual("durable", conn.execute("SELECT value FROM work").fetchone()[0])
        with self.assertRaises(FileExistsError):
            restore_database(copy, restored, stopped=True)


if __name__ == "__main__":
    unittest.main()
