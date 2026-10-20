"""Owner-operated, persistent reveal register, separate from research/study DBs.

This prevents accidental reuse, including after resetting a study ledger. It is
not a security boundary against someone who can replace these files or code.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import uuid
from datetime import date
from pathlib import Path

from .common import ResearchError, digest, now

PROTOCOL = 'local-daily-final-v1'
SCHEMA = 'holdout-register-v1'


def connect(path, expected_id=None, create=False):
    path = Path(path).resolve()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS registry_meta(schema TEXT, registry_id TEXT)')
            if not db.execute('SELECT 1 FROM registry_meta').fetchone():
                db.execute('INSERT INTO registry_meta VALUES(?,?)', (SCHEMA, str(uuid.uuid4())))
            db.execute('''CREATE TABLE IF NOT EXISTS reveal_claims(
                claim_key TEXT PRIMARY KEY, dataset_id TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, protocol TEXT NOT NULL,
                study_id TEXT NOT NULL, study_ledger TEXT NOT NULL, state TEXT NOT NULL,
                semantic_hash TEXT, created TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS reveals_dataset ON reveal_claims(dataset_id,start_date,end_date)')
            db.execute('CREATE INDEX IF NOT EXISTS reveals_semantic ON reveal_claims(semantic_hash)')
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction: db.execute('ROLLBACK')
            db.close()
            raise
    else:
        # mode=rw prevents a missing register from silently becoming an empty one.
        try: db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30, isolation_level=None)
        except sqlite3.Error as error: raise ResearchError('最终留出登记账本缺失；须恢复原账本，不能新建后重试') from error
    try:
        rows = db.execute('SELECT schema,registry_id FROM registry_meta').fetchall()
        if len(rows) != 1 or rows[0][0] != SCHEMA or (expected_id and rows[0][1] != expected_id):
            raise ResearchError('最终留出登记账本身份/版本不匹配；须恢复原账本')
        return db, rows[0][1]
    except BaseException:
        db.close()
        raise


def reserve(db, dataset_id, start, end, study_id, study_ledger, state='reserved'):
    # Protocol is fixed in code; changing study parameters cannot mint new rights.
    key = digest([dataset_id, start, end, PROTOCOL])
    try:
        db.execute('BEGIN IMMEDIATE')
        # Also prevent overlapping intervals, even if the declared protocol changes.
        existing = db.execute('''SELECT 1 FROM reveal_claims WHERE dataset_id=?
            AND start_date<=? AND end_date>=?''', (dataset_id, end, start)).fetchone()
        if existing:
            raise ResearchError('该 dataset_id 的最终留出已占用/揭示，或与已揭示区间重叠')
        db.execute('INSERT INTO reveal_claims VALUES(?,?,?,?,?,?,?,?,?,?)',
            (key, dataset_id, start, end, PROTOCOL, study_id, str(Path(study_ledger).resolve()), state, None, now()))
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction: db.execute('ROLLBACK')
        raise
    return key


def semantic_hash(path, start, end):
    """Fingerprint normalized interval observations, independent of row/column order.

    Called only AFTER consuming reveal permission. No evaluation or statistics.
    Numeric parsing matches the local kernel; it is auxiliary alias detection,
    not proof of dataset identity or point-in-time correctness.
    """
    from .local_validation import number
    path = Path(path)
    if path.stat().st_size > 100_000_000: raise ResearchError('本地 CSV 限制 100 MB')
    hashes = []
    with path.open(newline='', encoding='utf-8') as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames or []
        if len(set(columns)) != len(columns): raise ResearchError('CSV 列名重复')
        columns = sorted(columns)
        for row in reader:
            day = date.fromisoformat(row['date']).isoformat()
            if not start <= day <= end: continue
            normalized = {}
            for name in columns:
                value = row[name]
                if name in ('date', 'available_date'): value = date.fromisoformat(value).isoformat()
                elif name == 'session': value = int(value)
                elif name not in ('asset', 'eligible', 'subindustry'):
                    value = number(value)
                    if value == 0: value = 0.0
                normalized[name] = value
            hashes.append(digest(normalized))
            if len(hashes) > 100_000: raise ResearchError('本地面板行数上限 100000')
    if not hashes: raise ResearchError('最终留出范围没有数据')
    return digest({'normalization': 'local-panel-rows-v1', 'interval': [start, end], 'rows': sorted(hashes)})


def bind_semantic_hash(db, key, fingerprint):
    try:
        db.execute('BEGIN IMMEDIATE')
        duplicate = db.execute('SELECT 1 FROM reveal_claims WHERE semantic_hash=? AND claim_key<>?', (fingerprint, key)).fetchone()
        # Save even a blocked alias; it must never silently become available again.
        db.execute('UPDATE reveal_claims SET semantic_hash=?,state=? WHERE claim_key=?',
            (fingerprint, 'blocked_duplicate' if duplicate else 'revealing', key))
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction: db.execute('ROLLBACK')
        raise
    if duplicate: raise ResearchError('语义相同的最终留出数据已揭示/占用；更换文件或 dataset_id 不能重复使用')


def import_legacy(ledger_path, registry_path, dataset_id):
    """Conservatively consume all exposed v0.3.0 intervals for an attested dataset.

    The owner must supply the original dataset identity. No market observations
    or old final performance are read. Mixed datasets require separate reviewed
    source ledgers; automatic identity guessing is deliberately unsupported.
    """
    if not isinstance(dataset_id, str) or not dataset_id.strip(): raise ResearchError('缺少稳定 dataset_id')
    source = sqlite3.connect(Path(ledger_path).resolve().as_uri() + '?mode=rw', uri=True)
    db, registry_id = connect(registry_path, create=True)
    imported = 0
    try:
        rows = source.execute('''SELECT s.id,s.manifest FROM final_reveals r
            JOIN studies s ON s.id=r.study_id''').fetchall()
        for study_id, manifest in rows:
            plan = json.loads(manifest)['plan']
            start, end = plan['holdout_start'], plan['holdout_end']
            start,end=date.fromisoformat(start).isoformat(),date.fromisoformat(end).isoformat()
            if start>=end:raise ResearchError('旧账本的留出区间无效，须由负责人核对')
            key=digest([dataset_id,start,end,PROTOCOL])
            # Historical exposures may overlap. Preserve EACH whole interval;
            # merely skipping an overlap would leave its non-overlapping tail free.
            try:
                db.execute('BEGIN IMMEDIATE')
                cursor=db.execute('INSERT OR IGNORE INTO reveal_claims VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key,dataset_id,start,end,PROTOCOL,study_id,str(Path(ledger_path).resolve()),'legacy_exposed',None,now()))
                imported+=cursor.rowcount
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:db.execute('ROLLBACK')
                raise
        source.execute('CREATE TABLE IF NOT EXISTS legacy_reveal_imports(registry_id TEXT,dataset_id TEXT,created TEXT,PRIMARY KEY(registry_id,dataset_id))')
        source.execute('INSERT OR IGNORE INTO legacy_reveal_imports VALUES(?,?,?)', (registry_id, dataset_id, now()))
        source.commit()
    finally:
        db.close(); source.close()
    return {'imported_intervals': imported, 'dataset_id': dataset_id, 'registry_id': registry_id,
            'notice': '旧研究账本的数据身份须由负责人确认；没有读取或重新计算旧留出收益'}
