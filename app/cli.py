"""客服核对命令行：采集留证、三态答复、待核实清单与旧答复追查。

用法：
  python -m app.cli collect [--edition 1|2] [--blank rule:member-b]
  python -m app.cli answer <活动标识> <门店> <会员等级> [--edition 2]
  python -m app.cli pending <活动标识> <门店> 甲 乙 丙 [--edition 2]
  python -m app.cli trace <答复编号>
  python -m app.cli verify
"""
import json
import sys
from pathlib import Path

from .capture import EDITIONS
from .records import verify_chain
from .service import answer_query, collect_edition, recheck_pending, trace_answer

ORIGIN = 'http://127.0.0.1:8765'
DB_PATH = Path('data/evidence.sqlite3')
VERDICT_TEXT = {'available': '当前可用', 'unavailable': '明确不适用', 'pending': '待重新核实'}


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parse_blank(value):
    if ':' not in value:
        raise SystemExit('--blank 格式应为 类别:活动标识，例如 rule:member-b')
    part, activity = value.split(':', 1)
    if part not in ('catalog', 'scope', 'rule'):
        raise SystemExit('空白类别只能是 catalog、scope、rule')
    return part, activity


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else 'demo'

    if command == 'collect':
        edition = argv[argv.index('--edition') + 1] if '--edition' in argv else '2'
        if edition not in EDITIONS:
            raise SystemExit('edition 只能是 1 或 2')
        blank_parts = set()
        if '--blank' in argv:
            blank_parts.add(_parse_blank(argv[argv.index('--blank') + 1]))
        activity_ids = collect_edition(ORIGIN, DB_PATH, edition, blank_parts=blank_parts)
        _print({'collected_edition': edition, 'activities': activity_ids,
                'evidence_db': str(DB_PATH)})
        return

    if command == 'answer':
        activity_id, store, level = argv[1], argv[2], argv[3]
        edition = argv[argv.index('--edition') + 1] if '--edition' in argv else '2'
        verdict = answer_query(DB_PATH, activity_id, store, level, edition)
        verdict['verdict_text'] = VERDICT_TEXT[verdict['verdict']]
        _print(verdict)
        return

    if command == 'pending':
        activity_id, store = argv[1], argv[2]
        levels = []
        index = 3
        while index < len(argv):
            token = argv[index]
            if token == '--edition':
                index += 2
                continue
            if token.startswith('--'):
                index += 1
                continue
            levels.append(token)
            index += 1
        edition = argv[argv.index('--edition') + 1] if '--edition' in argv else '2'
        _print({'activity_id': activity_id, 'store': store,
                'pending_levels': recheck_pending(DB_PATH, activity_id, store, levels, edition)})
        return

    if command == 'trace':
        result = trace_answer(DB_PATH, int(argv[1]))
        if result is None:
            raise SystemExit('找不到该答复编号')
        _print(result)
        return

    if command == 'verify':
        broken = verify_chain(DB_PATH)
        _print({'evidence_db': str(DB_PATH),
                'chain_intact': not broken, 'broken_receipts': broken})
        return

    if command == 'demo':
        _demo()
        return

    raise SystemExit('未知命令：' + command)


def _demo():
    """端到端演练：两期发布、规则改变留证、空白页三态、重启追查。"""
    if DB_PATH.exists():
        DB_PATH.unlink()
    print('== 采集第 1 期发布并留证 ==')
    collect_edition(ORIGIN, DB_PATH, '1')
    print('== 采集第 2 期发布；member-b 的规则正文临时空白 ==')
    collect_edition(ORIGIN, DB_PATH, '2', blank_parts={('rule', 'member-b')})

    print('\n== 客服核对 member-a / 东城店 / 甲（目录与范围、规则互相矛盾）==')
    first = answer_query(DB_PATH, 'member-a', '东城店', '甲', '2')
    _print({k: first[k] for k in ('verdict', 'reason_text', 'basis_receipt_ids', 'answer_id')})

    print('\n== 客服核对 member-c / 西城店 / 丙（同一次有效发布支持）==')
    _print({k: answer_query(DB_PATH, 'member-c', '西城店', '丙', '2')[k]
            for k in ('verdict', 'reason_text')})

    print('\n== member-b 规则正文空白：未知不得写成无优惠 ==')
    _print({k: answer_query(DB_PATH, 'member-b', '南城店', '乙', '2')[k]
            for k in ('verdict', 'reason_text', 'last_valid_publication')})

    print('\n== 待重新核实的会员等级清单 ==')
    _print(recheck_pending(DB_PATH, 'member-b', '南城店', ['甲', '乙', '丙'], '2'))

    print('\n== 重启后追查第一条旧答复的依据，并校验哈希链 ==')
    _print(trace_answer(DB_PATH, first['answer_id']))


if __name__ == '__main__':
    main()
