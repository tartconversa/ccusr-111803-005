"""把同一次发布的三类观察组合成可核验发布，并据此给出三态优惠判定。

判定只有 available / unavailable / pending 三种：
- available：当前发布期的三类观察齐全且互相核验通过，门店与会员等级同时落在范围内；
- unavailable：发布有效但有明确证据表明该门店或会员等级不适用；
- pending：观察缺失、页面空白、来源故障、同次发布互相矛盾、或仅有历史发布可依。
未知永远是 pending，绝不写成"无优惠"。
"""
from .domain import KINDS, KIND_LABELS

VERDICTS = ('available', 'unavailable', 'pending')
PUBLICATION_STATES = ('valid', 'conflicting', 'incomplete')


def _checks(catalog, scope, rule):
    """对同一次发布的三类观察做交叉核验，返回不通过原因代码列表。"""
    reasons = []
    cat_facts = catalog['interpreted']['facts']
    scope_facts = scope['interpreted']['facts']
    rule_facts = rule['interpreted']['facts']
    if not (catalog['source_id'] == scope['source_id'] == rule['source_id']):
        reasons.append('identity_mismatch')
    if not (catalog['edition'] == scope['edition'] == rule['edition']):
        reasons.append('edition_mismatch')
    cat_level = cat_facts['level']
    if cat_level not in scope_facts['levels']:
        reasons.append('level_not_in_scope')
    if cat_level not in rule_facts['text']:
        reasons.append('level_missing_in_rule')
    benefit = cat_facts['benefit']
    if benefit not in rule_facts['text']:
        reasons.append('benefit_missing_in_rule')
    if not any(store in rule_facts['text'] for store in scope_facts['stores']):
        reasons.append('store_missing_in_rule')
    return reasons


def assemble_publication(observations):
    """observations 为同一活动同一发布期的 kind -> 观察记录(含 state)。

    观察记录的 state 为 observed / blank / error；后两者保留为证据但无法支撑发布。
    """
    kinds = sorted(observations)
    source_ids = {observations[k]['source_id'] for k in kinds}
    editions = {observations[k]['edition'] for k in kinds}
    source_id = next(iter(source_ids)) if len(source_ids) == 1 else None
    edition = next(iter(editions)) if len(editions) == 1 else None
    missing = [k for k in KINDS if k not in observations]
    unavailable_parts = [k for k in kinds if observations[k].get('state') != 'observed']
    reasons = []
    for k in missing:
        reasons.append('missing_' + k)
    for k in unavailable_parts:
        reasons.append('unavailable_' + k + ':' + str(observations[k].get('state')))
    observed = [k for k in KINDS if k in observations and observations[k].get('state') == 'observed']
    if missing or unavailable_parts:
        state = 'incomplete'
    else:
        reasons.extend(_checks(observations['catalog'], observations['scope'], observations['rule']))
        if len(source_ids) > 1 or len(editions) > 1:
            reasons.append('mixed_observations')
        state = 'valid' if not reasons else 'conflicting'
    return {
        'source_id': source_id, 'edition': edition, 'state': state,
        'observed_kinds': observed, 'missing_kinds': missing,
        'unavailable_parts': unavailable_parts, 'reasons': reasons,
        'receipt_ids': {k: observations[k].get('receipt_id') for k in kinds},
    }


def _scope_of(publication, observations):
    return observations['scope']['interpreted']['facts']


def evaluate(activity_id, store, level, current_edition, publications, observations_by_pub):
    """依据各发布期的核验结论给出 (门店, 会员等级) 的优惠判定。

    publications: edition -> assemble_publication 的结果
    observations_by_pub: edition -> kind -> 观察记录
    """
    current = publications.get(current_edition)
    history = [pub for ed, pub in publications.items()
               if ed != current_edition and pub['state'] == 'valid']
    history = sorted(history, key=lambda p: p['edition'], reverse=True)

    def pending(reason, publication=None):
        return {'activity_id': activity_id, 'store': store, 'level': level,
                'verdict': 'pending', 'reason': reason,
                'basis_receipt_ids': {},
                'evidence_receipt_ids': dict(publication['receipt_ids']) if publication else {},
                'last_valid_publication': history[0] if history else None}

    if current is None:
        return pending('no_publication_observed')
    if current['state'] != 'valid':
        return pending('current_publication_' + current['state'], current)

    observations = observations_by_pub.get(current_edition, {})
    if 'scope' not in observations or observations['scope'].get('state') != 'observed':
        return pending('current_publication_incomplete', current)
    scope = _scope_of(current, observations)
    store_ok = store in scope['stores']
    level_ok = level in scope['levels']
    receipt_ids = dict(current['receipt_ids'])
    if store_ok and level_ok:
        return {'activity_id': activity_id, 'store': store, 'level': level,
                'verdict': 'available', 'reason': 'within_scope',
                'basis_receipt_ids': receipt_ids, 'evidence_receipt_ids': {},
                'last_valid_publication': current}
    return {'activity_id': activity_id, 'store': store, 'level': level,
            'verdict': 'unavailable',
            'reason': ('store_excluded' if not store_ok else 'level_excluded'),
            'basis_receipt_ids': receipt_ids, 'evidence_receipt_ids': {},
            'last_valid_publication': current}


def pending_levels(activity_id, store, level_candidates, current_edition,
                   publications, observations_by_pub):
    """列出仍待重新核实的会员等级：判定为 pending 的等级，附最近一次有效发布作为追查线索。"""
    result = []
    for level in level_candidates:
        verdict = evaluate(activity_id, store, level, current_edition,
                           publications, observations_by_pub)
        if verdict['verdict'] == 'pending':
            result.append({'level': level, 'reason': verdict['reason'],
                           'last_valid_publication': verdict['last_valid_publication']})
    return result


def explain_reason(code):
    """把原因代码翻译成客服可读的中文说明。"""
    table = {
        'identity_mismatch': '三类观察的活动标识不一致',
        'edition_mismatch': '观察来自不同发布期，不能凑成同一次发布',
        'level_missing_in_rule': '规则正文未包含目录声明的会员等级',
        'level_not_in_scope': '目录声明的会员等级不在适用范围内',
        'benefit_missing_in_rule': '规则正文未包含目录声明的优惠类型',
        'store_missing_in_rule': '规则正文未包含适用范围中的任何门店',
        'mixed_observations': '不同范围或发布期的观察被混在一起，拒绝合并',
        'no_publication_observed': '当前发布期尚无任何观察',
        'within_scope': '门店与会员等级均在同一次有效发布的适用范围内',
        'store_excluded': '有效发布明确排除了该门店',
        'level_excluded': '有效发布明确排除了该会员等级',
    }
    if code in table:
        return table[code]
    if code.startswith('missing_'):
        return KIND_LABELS.get(code[8:], code[8:]) + '观察缺失'
    if code.startswith('unavailable_'):
        rest = code[len('unavailable_'):]
        kind, _, state = rest.partition(':')
        state_text = {'blank': '页面空白', 'error': '来源故障'}.get(state, state)
        return KIND_LABELS.get(kind, kind) + state_text + '，无法读取'
    if code.startswith('current_publication_'):
        state = code[len('current_publication_'):]
        state_text = {'conflicting': '内容互相矛盾', 'incomplete': '观察不完整'}.get(state, state)
        return '当前发布' + state_text + '，需重新核实'
    return code
