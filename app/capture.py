"""从本地来源独立采集目录、适用范围、规则正文三类观察。

页面空白或来源故障不会被解释成"没有优惠"，而是原样保存为 state=blank/error 的观察收据；
能否支撑优惠由 app.policy 的发布核验决定，采集层不做业务结论。
"""
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from .domain import INTERPRETERS, fingerprint

EDITIONS = ('1', '2')


def _now():
    return datetime.now(timezone.utc).isoformat()


def get(origin, path, query):
    url = urlsplit(origin)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('只允许本地演练来源')
    address = origin.rstrip('/') + path + '?' + urlencode(query)
    with urlopen(address, timeout=5) as response:
        return address, json.load(response)


def _build(kind, source_id, edition, url, raw, state, clock):
    observation = {'source_id': source_id, 'edition': edition, 'kind': kind,
                   'state': state, 'raw': raw, 'observed_at': clock(), 'url': url}
    observation['content_hash'] = fingerprint(raw)
    if state == 'observed':
        observation['interpreted'] = INTERPRETERS[kind](raw)
    return observation


def _read(origin, path, query, kind, source_id, edition, clock):
    address = origin.rstrip('/') + path + '?' + urlencode(query)
    try:
        with urlopen(address, timeout=5) as response:
            raw = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return _build(kind, source_id, edition, address,
                      {'error': type(exc).__name__}, 'error', clock)
    if isinstance(raw, dict) and raw.get('blank') is True:
        return _build(kind, source_id, edition, address, raw, 'blank', clock)
    return _build(kind, source_id, edition, address, raw, 'observed', clock)


def read_catalog(origin, edition='1', clock=_now, blank=False):
    """目录列表拆成逐条活动的目录观察；整页空白时返回一条不带活动标识的空白观察。"""
    query = {'edition': edition}
    if blank:
        query['blank'] = 'catalog'
    address = origin.rstrip('/') + '/catalog?' + urlencode(query)
    try:
        with urlopen(address, timeout=5) as response:
            raw = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [_build('catalog', None, edition, address,
                       {'error': type(exc).__name__}, 'error', clock)]
    if isinstance(raw, dict) and raw.get('blank') is True:
        return [_build('catalog', None, edition, address, raw, 'blank', clock)]
    observations = []
    for row in raw['items']:
        row = dict(row, edition=raw.get('edition', edition))
        observations.append(_build('catalog', row['id'], edition, address, row, 'observed', clock))
    return observations


def read_catalog_entry(origin, item_id, edition='1', clock=_now, blank=False):
    """读取单个活动在目录中的观察；目录整页空白时为该活动生成空白观察。"""
    if blank:
        address = origin.rstrip('/') + '/catalog?' + urlencode({'edition': edition, 'blank': 'catalog'})
        try:
            with urlopen(address, timeout=5) as response:
                raw = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            return _build('catalog', item_id, edition, address,
                          {'error': type(exc).__name__}, 'error', clock)
        return _build('catalog', item_id, edition, address, raw, 'blank', clock)
    for observation in read_catalog(origin, edition, clock):
        if observation['source_id'] == item_id:
            return observation
    raise ValueError('目录中找不到活动 ' + item_id)


def read_scope(origin, item_id, edition='1', clock=_now, blank=False):
    query = {'id': item_id, 'edition': edition}
    if blank:
        query['blank'] = 'scope'
    return _read(origin, '/scope', query, 'scope', item_id, edition, clock)


def read_detail(origin, item_id, edition='1', clock=_now, blank=False):
    query = {'id': item_id, 'edition': edition}
    if blank:
        query['blank'] = 'rule'
    return _read(origin, '/detail', query, 'rule', item_id, edition, clock)


def observe_activity(origin, item_id, edition='1', clock=_now, blank_parts=()):
    """采集单个活动某次发布的全部三类观察，互不补齐。"""
    return {
        'catalog': read_catalog_entry(origin, item_id, edition, clock,
                                      blank='catalog' in blank_parts),
        'scope': read_scope(origin, item_id, edition, clock, blank='scope' in blank_parts),
        'rule': read_detail(origin, item_id, edition, clock, blank='rule' in blank_parts),
    }
