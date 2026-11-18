"""来源观察、发布核验、证据留痕与客服追查的回归。"""
import json
import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from urllib.request import urlopen

from app.capture import observe_activity, read_catalog, read_detail, read_scope
from app.domain import KIND_LABELS, fingerprint, interpret_catalog, interpret_scope
from app.policy import assemble_publication, evaluate, explain_reason
from app.records import (get_observation, list_observations, record_answer,
                         save_observation, verify_chain)
from app.service import (answer_query, collect_edition, publications_for,
                         recheck_pending, refresh_publication, trace_answer)
from app.site import DATA, start_server


class SourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_server()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / 'evidence.sqlite3'
        self.ticks = count(1)
        self.clock = lambda: f'2026-01-01T00:00:{next(self.ticks):02d}+00:00'

    def tearDown(self):
        self.temporary.cleanup()

    def test_three_independent_observations(self):
        """目录、适用范围、规则正文是三类独立观察，各自带内容指纹与身份。"""
        observations = observe_activity(self.origin, 'member-a', '1', self.clock)
        self.assertEqual(set(observations), {'catalog', 'scope', 'rule'})
        for observation in observations.values():
            self.assertEqual(observation['state'], 'observed')
            self.assertEqual(observation['edition'], '1')
            self.assertEqual(observation['content_hash'],
                             fingerprint(observation['raw']))
        self.assertEqual(observations['catalog']['interpreted']['facts']['benefit'], '满额返券')
        self.assertIn('东城店', observations['scope']['interpreted']['facts']['stores'])
        self.assertIn('满额返券', observations['rule']['interpreted']['facts']['text'])

    def test_rule_change_keeps_append_only_evidence(self):
        """规则改变产生新收据，旧收据不被覆盖，哈希链完整。"""
        first = read_detail(self.origin, 'member-a', '1', self.clock)
        second = read_detail(self.origin, 'member-a', '2', self.clock)
        id1, added1 = save_observation(self.db, first)
        id2, added2 = save_observation(self.db, second)
        self.assertTrue(added1 and added2 and id1 != id2)
        # 重复读取相同内容是幂等的，不产生新收据
        _, added_again = save_observation(self.db, first)
        self.assertFalse(added_again)
        receipts = list_observations(self.db, 'member-a')
        self.assertEqual(len(receipts), 2)
        self.assertNotEqual(receipts[0]['content_hash'], receipts[1]['content_hash'])
        self.assertEqual(verify_chain(self.db), [])

    def test_tampered_receipt_is_detected(self):
        """篡改证据（含原始内容或收据字段）都会被哈希链校验发现。"""
        import sqlite3
        save_observation(self.db, read_detail(self.origin, 'member-a', '1', self.clock))
        save_observation(self.db, read_scope(self.origin, 'member-a', '1', self.clock))
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE observations SET content_hash='x' WHERE receipt_id=2")
        self.assertEqual(verify_chain(self.db), [2])
        # 只改 payload 中的簿记外字段（如 URL）也必须被发现
        with sqlite3.connect(self.db) as db:
            row = db.execute('SELECT payload FROM observations WHERE receipt_id=1').fetchone()
            tampered = json.loads(row[0])
            tampered['url'] = tampered['url'] + '&tampered=1'
            db.execute('UPDATE observations SET payload=? WHERE receipt_id=1',
                       (json.dumps(tampered, ensure_ascii=False),))
        self.assertEqual(verify_chain(self.db), [1, 2])

    def test_blank_page_is_unknown_not_no_offer(self):
        """临时页面空白记录为 blank 观察，不能被解释成无优惠。"""
        blank_rule = read_detail(self.origin, 'member-b', '2', self.clock, blank=True)
        self.assertEqual(blank_rule['state'], 'blank')
        rid, _ = save_observation(self.db, blank_rule)
        stored = get_observation(self.db, rid)
        self.assertEqual(stored['state'], 'blank')
        # 目录整页空白同样是空白观察，且不伪造任何活动身份
        blank_catalog = read_catalog(self.origin, '2', self.clock, blank=True)
        self.assertEqual(len(blank_catalog), 1)
        self.assertEqual(blank_catalog[0]['state'], 'blank')
        self.assertIsNone(blank_catalog[0]['source_id'])
        # 来源故障记录为 error 观察，而不是抛出或当成空结果
        faulty = read_scope('http://127.0.0.1:9', 'member-a', '2', self.clock)
        self.assertEqual(faulty['state'], 'error')
        self.assertEqual(faulty['kind'], 'scope')

    def test_valid_publication_supports_available_verdict(self):
        """三类观察齐全且互相核验通过，且门店与等级都在范围内才是 available。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        verdict = answer_query(self.db, 'member-a', '东城店', '甲', current_edition='1', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'available')
        self.assertEqual(set(verdict['basis_receipt_ids']), {'catalog', 'scope', 'rule'})
        # 门店退出活动后是有据可查的 unavailable，而不是 pending
        left = answer_query(self.db, 'member-c', '东城店', '丙', current_edition='1', clock=self.clock)
        self.assertEqual(left['verdict'], 'unavailable')
        self.assertEqual(left['reason'], 'store_excluded')
        # 会员等级被排除同样是有据可查的 unavailable
        wrong_level = answer_query(self.db, 'member-a', '东城店', '乙', current_edition='1', clock=self.clock)
        self.assertEqual(wrong_level['verdict'], 'unavailable')
        self.assertEqual(wrong_level['reason'], 'level_excluded')

    def test_conflicting_publication_is_pending_with_evidence(self):
        """目录仍是满额返券/甲，范围与规则已改成乙/折扣：同次发布矛盾，只能 pending。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        collect_edition(self.origin, self.db, '2', self.clock)
        verdict = answer_query(self.db, 'member-a', '东城店', '甲', current_edition='2', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'pending')
        self.assertEqual(verdict['reason'], 'current_publication_conflicting')
        publication = publications_for(self.db, 'member-a')['2']
        self.assertEqual(publication['state'], 'conflicting')
        self.assertIn('level_not_in_scope', publication['reasons'])
        self.assertIn('benefit_missing_in_rule', publication['reasons'])
        # 矛盾的三类收据仍然各自留痕，可人工追查
        self.assertEqual(len(publication['receipt_ids']), 3)
        # 第 1 期有效发布仍可作为旧答复依据被追查
        self.assertEqual(publications_for(self.db, 'member-a')['1']['state'], 'valid')

    def test_blank_rule_makes_publication_incomplete_and_pending(self):
        """规则正文空白导致发布不完整，判定必须是 pending 而非无优惠。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        collect_edition(self.origin, self.db, '2', self.clock,
                        blank_parts={('rule', 'member-b')})
        publication = publications_for(self.db, 'member-b')['2']
        self.assertEqual(publication['state'], 'incomplete')
        self.assertIn('unavailable_rule:blank', publication['reasons'])
        verdict = answer_query(self.db, 'member-b', '南城店', '乙', current_edition='2', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'pending')
        # 仍能追到第 1 期的有效发布，知道旧答复曾依据什么
        self.assertIsNotNone(verdict['last_valid_publication'])
        self.assertEqual(verdict['last_valid_publication']['edition'], '1')

    def test_no_cross_edition_mixing(self):
        """不同发布期、不同范围的条件不能凑成一张优惠。"""
        # 只采集第 1 期目录和范围，规则正文缺失
        save_observation(self.db, observe_activity(self.origin, 'member-c', '1', self.clock)['catalog'])
        save_observation(self.db, observe_activity(self.origin, 'member-c', '1', self.clock)['scope'])
        publication = refresh_publication(self.db, 'member-c', '1', self.clock)
        self.assertEqual(publication['state'], 'incomplete')
        self.assertIn('missing_rule', publication['reasons'])
        verdict = answer_query(self.db, 'member-c', '西城店', '丙', current_edition='1', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'pending')

    def test_pending_levels_report(self):
        """客服能看到哪一类会员仍待重新核实。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        collect_edition(self.origin, self.db, '2', self.clock,
                        blank_parts={('rule', 'member-b')})
        pending = recheck_pending(self.db, 'member-b', '南城店', ['甲', '乙', '丙'], '2')
        levels = {item['level'] for item in pending}
        self.assertIn('乙', levels)
        for item in pending:
            self.assertIsNotNone(item['last_valid_publication'])

    def test_answer_traceable_after_restart(self):
        """重启后（新连接同一数据库）可追查旧答复依据，且证据链完整。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        verdict = answer_query(self.db, 'member-a', '西城店', '甲', current_edition='1', clock=self.clock)
        answer_id = verdict['answer_id']
        traced = trace_answer(self.db, answer_id)
        self.assertEqual(traced['answer']['verdict'], 'available')
        self.assertEqual(traced['chain_broken_receipts'], [])
        kinds = {key.split(':', 1)[1] for key in traced['evidence']}
        self.assertEqual(kinds, {'catalog', 'scope', 'rule'})
        for key, item in traced['evidence'].items():
            self.assertTrue(key.startswith('basis:'))
            self.assertEqual(item['observation']['kind'], key.split(':', 1)[1])
        self.assertTrue(explain_reason(traced['answer']['reason']))

    def test_pending_answer_keeps_conflicting_evidence(self):
        """pending 答复也必须留痕其上下文证据，重启后能看到矛盾来自哪些收据。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        collect_edition(self.origin, self.db, '2', self.clock)
        verdict = answer_query(self.db, 'member-a', '东城店', '甲', current_edition='2', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'pending')
        self.assertEqual(set(verdict['evidence_receipt_ids']), {'catalog', 'scope', 'rule'})
        traced = trace_answer(self.db, verdict['answer_id'])
        context = {key for key in traced['evidence'] if key.startswith('context:')}
        self.assertEqual(len(context), 3)
        # 矛盾证据里能直接读到目录仍写着满额返券
        catalog = traced['evidence']['context:catalog']['observation']
        self.assertEqual(catalog['interpreted']['facts']['benefit'], '满额返券')

    def test_unknown_activity_is_pending(self):
        """从未采集过的活动是未知（pending），而不是无优惠。"""
        collect_edition(self.origin, self.db, '1', self.clock)
        verdict = answer_query(self.db, 'member-x', '东城店', '甲', current_edition='2', clock=self.clock)
        self.assertEqual(verdict['verdict'], 'pending')
        self.assertEqual(verdict['reason'], 'no_publication_observed')

    def test_remote_rejected(self):
        from app.capture import get
        with self.assertRaises(ValueError):
            get('https://example.com', '/catalog', {'edition': '1'})

    def test_catalog_page_still_served(self):
        with urlopen(self.origin + '/catalog.html?edition=2') as response:
            self.assertIn(DATA['title'], response.read().decode('utf-8'))

    def test_interpreters_reject_incomplete_facts(self):
        with self.assertRaises(ValueError):
            interpret_catalog({'id': 'x', 'edition': '1'})
        with self.assertRaises(ValueError):
            interpret_scope({'id': 'x', 'edition': '1', 'stores': [], 'levels': ['甲']})

    def test_reason_codes_are_explained(self):
        for code in ('identity_mismatch', 'edition_mismatch', 'level_not_in_scope',
                     'benefit_missing_in_rule', 'store_missing_in_rule',
                     'missing_rule', 'unavailable_rule:blank',
                     'current_publication_conflicting', 'store_excluded'):
            self.assertTrue(explain_reason(code))
        self.assertIn(KIND_LABELS['rule'], explain_reason('missing_rule'))

    def test_assembly_rejects_mixed_identity(self):
        good = observe_activity(self.origin, 'member-a', '1', self.clock)
        foreign = observe_activity(self.origin, 'member-b', '1', self.clock)['scope']
        mixed = dict(good, scope=foreign)
        publication = assemble_publication(mixed)
        self.assertEqual(publication['state'], 'conflicting')
        self.assertIn('identity_mismatch', publication['reasons'])

    def test_editions_keep_separate_publications(self):
        collect_edition(self.origin, self.db, '1', self.clock)
        collect_edition(self.origin, self.db, '2', self.clock)
        pubs = publications_for(self.db, 'member-c')
        self.assertEqual(set(pubs), {'1', '2'})
        # member-c 两期都有效，但第 2 期门店退出后范围缩小
        v2 = evaluate('member-c', '南城店', '丙', '2', pubs,
                      {'2': {k: get_observation(self.db, rid) for k, rid in
                             pubs['2']['receipt_ids'].items()}})
        self.assertEqual(v2['verdict'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
