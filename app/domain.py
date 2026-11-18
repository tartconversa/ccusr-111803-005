"""解释目录、适用范围、规则正文三类观察的业务身份与事实。

三类观察各自独立，任何一类缺失或空白都不能由另外两类补齐：
目录说明"面向谁、是什么优惠"，适用范围说明"在哪些门店、哪些会员等级成立"，
规则正文是对外解释原文。身份只用稳定标识 source_id 与发布期 edition。
"""
import hashlib
import json

KINDS = ('catalog', 'scope', 'rule')
KIND_LABELS = {'catalog': '目录', 'scope': '适用范围', 'rule': '规则正文'}


def fingerprint(payload):
    """对观察到的原始内容做内容指纹，规范序列化保证可重复计算。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


# 不参与整份收据指纹的簿记字段
RECEIPT_BOOKKEEPING = ('receipt_id', 'receipt_hash')


def receipt_fingerprint(observation):
    """对除簿记字段外的整份收据内容取指纹，任何字段被篡改都能被发现。"""
    material = {key: value for key, value in observation.items()
                if key not in RECEIPT_BOOKKEEPING}
    return fingerprint(material)


def _identity(row, kind):
    if not row.get('id'):
        raise ValueError(kind + '身份不完整：缺少活动标识')
    if str(row.get('edition', '')) not in ('1', '2'):
        raise ValueError(kind + '身份不完整：发布期无效')
    return {'source_id': row['id'], 'edition': str(row['edition']), 'kind': kind}


def interpret_catalog(row):
    identity = _identity(row, 'catalog')
    if not row.get('name') or not row.get('level') or not row.get('benefit'):
        raise ValueError('目录身份不完整：名称、会员等级或优惠类型缺失')
    return dict(identity, name=row['name'], group=row.get('group', ''),
                facts={'level': row['level'], 'benefit': row['benefit'],
                       'summary': row.get('summary', '')})


def interpret_scope(row):
    identity = _identity(row, 'scope')
    stores, levels = row.get('stores'), row.get('levels')
    if not isinstance(stores, list) or not stores:
        raise ValueError('适用范围不完整：缺少适用门店')
    if not isinstance(levels, list) or not levels:
        raise ValueError('适用范围不完整：缺少会员等级')
    return dict(identity, facts={'stores': list(stores), 'levels': list(levels)})


def interpret_rule(row):
    identity = _identity(row, 'rule')
    text = row.get('detail')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('规则正文不完整：内容为空')
    return dict(identity, facts={'text': text.strip()})


INTERPRETERS = {'catalog': interpret_catalog, 'scope': interpret_scope, 'rule': interpret_rule}
