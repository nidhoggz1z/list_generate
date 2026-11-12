"""Durable FIFO submissions; a new submission never cancels an older one."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .task_models import CreateTaskRequest


class StorageOwnership:
    def __init__(self, root):
        self.root = Path(root)

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / 'owner.sqlite3', timeout=0, isolation_level=None)
        try:
            self.connection.execute('BEGIN EXCLUSIVE')
        except sqlite3.Error:
            self.connection.close()
            raise RuntimeError('同一任务存储只允许一个包装服务进程使用') from None
        return self

    def __exit__(self, *args):
        self.connection.close()


class TaskStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'gateway.sqlite3'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS submissions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                generationId TEXT UNIQUE NOT NULL,
                bsnEvtId TEXT NOT NULL, userNo TEXT NOT NULL, taskId TEXT NOT NULL,
                businessData TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED')),
                result TEXT, reason TEXT, workflowRunId TEXT, createdAt TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS submission_business ON submissions(bsnEvtId, sequence)')
            db.execute('CREATE INDEX IF NOT EXISTS submission_queue ON submissions(status, sequence)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('businessData','result'):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def accept(self, business):
        business = CreateTaskRequest.model_validate(business).model_dump(mode='json')
        generation = uuid4().hex
        with self.connection() as db:
            db.execute('INSERT INTO submissions(generationId,bsnEvtId,userNo,taskId,businessData,status,createdAt) '
                       "VALUES (?,?,?,?,?,'PENDING',?)", (generation, business['bsnEvtId'],business['userNo'],
                       business['taskId'],json.dumps(business,ensure_ascii=False,allow_nan=False),
                       datetime.now(timezone.utc).isoformat()))
            return self.decode(db.execute('SELECT * FROM submissions WHERE generationId=?',(generation,)).fetchone())

    def claim(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM submissions WHERE status='PENDING' ORDER BY sequence LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE submissions SET status='PROCESSING' WHERE generationId=?",(row['generationId'],))
            return self.decode(dict(row)|dict(status='PROCESSING'))

    def get(self, generation):
        with self.connection() as db:
            return self.decode(db.execute('SELECT * FROM submissions WHERE generationId=?',(generation,)).fetchone())

    def is_latest(self, generation):
        with self.connection() as db:
            return bool(db.execute('''SELECT 1 FROM submissions s WHERE s.generationId=? AND NOT EXISTS
                (SELECT 1 FROM submissions n WHERE n.bsnEvtId=s.bsnEvtId AND n.sequence>s.sequence)''',
                (generation,)).fetchone())

    def finish(self, generation, *, result=None, reason=None):
        status = 'DONE' if result is not None else 'FAILED'
        with self.connection() as db:
            db.execute("UPDATE submissions SET status=?,result=?,reason=? WHERE generationId=? AND status='PROCESSING'",
                       (status,json.dumps(result,ensure_ascii=False,allow_nan=False) if result is not None else None,
                        reason,generation))
        return self.get(generation)

    def save_run_id(self, generation, run_id):
        with self.connection() as db:
            db.execute('UPDATE submissions SET workflowRunId=? WHERE generationId=?',(run_id,generation))

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM submissions WHERE status IN ('PENDING','PROCESSING') ORDER BY sequence").fetchall()
            db.execute("UPDATE submissions SET status='FAILED',reason='服务中断，任务未完成' WHERE status IN ('PENDING','PROCESSING')")
        return [self.decode(dict(row)|dict(status='FAILED',reason='服务中断，任务未完成')) for row in rows]

    def counts(self):
        with self.connection() as db:
            return {row['status']:row['count'] for row in db.execute('SELECT status,COUNT(*) count FROM submissions GROUP BY status')}
