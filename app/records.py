"""追加式证据存储：观察收据、发布核验结论与客服答复留痕。

观察收据只增不改：同一活动同一发布期的规则改变会留下新的收据而不是覆盖旧收据。
每条收据携带内容指纹与前一条收据的指纹，形成哈希链，重启后可验证证据是否被篡改。
"""
import json
import sqlite3
from pathlib import Path

from .domain import fingerprint, receipt_fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(
  receipt_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  edition TEXT NOT NULL,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  receipt_hash TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  payload TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  url TEXT NOT NULL,
  UNIQUE(source_id, edition, kind, content_hash, state)
);
CREATE TABLE IF NOT EXISTS publications(
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  edition TEXT NOT NULL,
  state TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  detail TEXT NOT NULL,
  UNIQUE(source_id, edition, decided_at)
);
CREATE TABLE IF NOT EXISTS answers(
  answer_id INTEGER PRIMARY KEY,
  activity_id TEXT NOT NULL,
  store TEXT NOT NULL,
  level TEXT NOT NULL,
  verdict TEXT NOT NULL,
  reason TEXT NOT NULL,
  basis_receipt_ids TEXT NOT NULL,
  evidence_receipt_ids TEXT NOT NULL DEFAULT '{}',
  answered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress(name TEXT PRIMARY KEY, last_page TEXT NOT NULL);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    # 兼容早期只含 content_hash 的证据库
    columns = {row[1] for row in db.execute('PRAGMA table_info(observations)')}
    if 'receipt_hash' not in columns:
        db.execute('ALTER TABLE observations ADD COLUMN receipt_hash TEXT NOT NULL DEFAULT ""')
    answer_columns = {row[1] for row in db.execute('PRAGMA table_info(answers)')}
    if answer_columns and 'evidence_receipt_ids' not in answer_columns:
        db.execute("ALTER TABLE answers ADD COLUMN evidence_receipt_ids TEXT NOT NULL DEFAULT '{}'")
    return db


def save_observation(path, observation):
    """保存一条观察收据；内容指纹已存在时幂等返回旧收据，否则追加并续接哈希链。"""
    db = connect(path)
    try:
        existing = db.execute(
            'SELECT receipt_id FROM observations WHERE source_id=? AND edition=? AND kind=? '
            'AND content_hash=? AND state=?',
            (observation['source_id'], observation['edition'], observation['kind'],
             observation['content_hash'], observation['state'])).fetchone()
        if existing:
            stored = json.loads(db.execute(
                'SELECT payload FROM observations WHERE receipt_id=?', (existing[0],)).fetchone()[0])
            stored['receipt_id'] = existing[0]
            return existing[0], False
        prev = db.execute('SELECT content_hash FROM observations ORDER BY receipt_id DESC LIMIT 1').fetchone()
        prev_hash = prev[0] if prev else ''
        receipt_hash = receipt_fingerprint(observation)
        cur = db.execute(
            'INSERT INTO observations(source_id,edition,kind,state,content_hash,receipt_hash,prev_hash,'
            'payload,observed_at,url) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (observation['source_id'], observation['edition'], observation['kind'],
             observation['state'], observation['content_hash'], receipt_hash, prev_hash,
             json.dumps(observation, ensure_ascii=False), observation['observed_at'], observation['url']))
        observation['receipt_id'] = cur.lastrowid
        observation['receipt_hash'] = receipt_hash
        db.execute('UPDATE observations SET payload=? WHERE receipt_id=?',
                   (json.dumps(observation, ensure_ascii=False), cur.lastrowid))
        db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',
                   ('observation:' + observation['kind'], observation['url']))
        db.commit()
        return cur.lastrowid, True
    finally:
        db.close()


def list_observations(path, source_id=None, edition=None, kind=None):
    db = connect(path)
    try:
        sql, params = 'SELECT receipt_id,payload FROM observations WHERE 1=1', []
        if source_id is not None:
            sql += ' AND source_id=?'; params.append(source_id)
        if edition is not None:
            sql += ' AND edition=?'; params.append(edition)
        if kind is not None:
            sql += ' AND kind=?'; params.append(kind)
        sql += ' ORDER BY receipt_id'
        observations = []
        for receipt_id, payload in db.execute(sql, params):
            observation = json.loads(payload)
            observation['receipt_id'] = receipt_id
            observations.append(observation)
        return observations
    finally:
        db.close()


def get_observation(path, receipt_id):
    db = connect(path)
    try:
        row = db.execute('SELECT receipt_id,payload FROM observations WHERE receipt_id=?', (receipt_id,)).fetchone()
        if not row:
            return None
        observation = json.loads(row[1])
        observation['receipt_id'] = row[0]
        return observation
    finally:
        db.close()


def verify_chain(path):
    """重算原始内容指纹、整份收据指纹与哈希链，返回被篡改的收据编号列表；空列表表示证据完整。"""
    db = connect(path)
    try:
        broken, prev_hash = [], ''
        for receipt_id, payload, stored_hash, receipt_hash, stored_prev in db.execute(
                'SELECT receipt_id,payload,content_hash,receipt_hash,prev_hash '
                'FROM observations ORDER BY receipt_id'):
            observation = json.loads(payload)
            raw_intact = fingerprint(observation.get('raw', observation)) == stored_hash
            receipt_intact = (not receipt_hash
                              or receipt_fingerprint(observation) == receipt_hash)
            chain_intact = stored_prev == prev_hash
            if not (raw_intact and receipt_intact and chain_intact):
                broken.append(receipt_id)
            prev_hash = stored_hash
        return broken
    finally:
        db.close()


def save_publication(path, publication, decided_at):
    db = connect(path)
    try:
        db.execute('INSERT OR REPLACE INTO publications(source_id,edition,state,decided_at,detail) '
                   'VALUES(?,?,?,?,?)',
                   (publication['source_id'], publication['edition'], publication['state'],
                    decided_at, json.dumps(publication, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()


def latest_publications(path):
    """每个活动每个发布期最近一次核验结论。"""
    db = connect(path)
    try:
        rows = db.execute(
            'SELECT detail FROM publications WHERE rowid IN '
            '(SELECT MAX(rowid) FROM publications GROUP BY source_id,edition)').fetchall()
        return [json.loads(row[0]) for row in rows]
    finally:
        db.close()


def record_answer(path, verdict, answered_at):
    """客服答复留痕：记录答复结论、依据收据与上下文证据，供重启后追查。"""
    db = connect(path)
    try:
        cur = db.execute(
            'INSERT INTO answers(activity_id,store,level,verdict,reason,basis_receipt_ids,'
            'evidence_receipt_ids,answered_at) VALUES(?,?,?,?,?,?,?,?)',
            (verdict['activity_id'], verdict['store'], verdict['level'], verdict['verdict'],
             verdict['reason'], json.dumps(verdict.get('basis_receipt_ids', {}), ensure_ascii=False),
             json.dumps(verdict.get('evidence_receipt_ids', {}), ensure_ascii=False),
             answered_at))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def list_answers(path):
    db = connect(path)
    try:
        return [{'answer_id': row[0], 'activity_id': row[1], 'store': row[2], 'level': row[3],
                 'verdict': row[4], 'reason': row[5],
                 'basis_receipt_ids': json.loads(row[6]),
                 'evidence_receipt_ids': json.loads(row[7]), 'answered_at': row[8]}
                for row in db.execute(
                    'SELECT answer_id,activity_id,store,level,verdict,reason,basis_receipt_ids,'
                    'evidence_receipt_ids,answered_at FROM answers ORDER BY answer_id')]
    finally:
        db.close()


def list_receipts(path):
    """兼容旧接口：返回全部观察收据。"""
    return list_observations(path)
