"""采集编排与客服工作流：证据入库、发布核验、三态答复、重启追查。"""
from datetime import datetime, timezone

from .capture import EDITIONS, read_catalog, read_detail, read_scope
from .policy import assemble_publication, evaluate, explain_reason, pending_levels
from .records import (get_observation, latest_publications, list_answers,
                      list_observations, record_answer, save_observation,
                      save_publication, verify_chain)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _latest_observations(path, source_id, edition):
    """取同一活动同一发布期每类观察的最新一条收据（旧收据仍保留作历史证据）。"""
    grouped = {}
    for observation in list_observations(path, source_id, edition):
        grouped[observation['kind']] = observation
    return grouped


def collect_edition(origin, path, edition, clock=_now, blank_parts=()):
    """采集整期目录，并为每个活动补齐适用范围与规则正文，逐类留证后核验发布。

    blank_parts 为 (类别, 活动标识) 元组集合；目录空白的活动标识用 None。
    """
    blank_parts = set(blank_parts)
    catalog_blank = any(part == 'catalog' for part, _ in blank_parts)
    catalog_entries = read_catalog(origin, edition, clock, blank=catalog_blank)
    activity_ids = []
    for entry in catalog_entries:
        if entry['source_id'] is None:
            save_observation(path, entry)
            continue
        activity_ids.append(entry['source_id'])
        save_observation(path, entry)
        per_activity = {part for part, activity in blank_parts if activity == entry['source_id']}
        for kind, reader in (('scope', read_scope), ('rule', read_detail)):
            observation = reader(origin, entry['source_id'], edition, clock,
                                 blank=kind in per_activity)
            save_observation(path, observation)
    for activity_id in activity_ids:
        refresh_publication(path, activity_id, edition, clock)
    return activity_ids


def refresh_publication(path, activity_id, edition, clock=_now):
    """用最新观察重新核验同一次发布；旧结论保留，新结论按时间追加。"""
    grouped = _latest_observations(path, activity_id, edition)
    publication = assemble_publication(grouped)
    save_publication(path, publication, clock())
    return publication


def publications_for(path, activity_id):
    return {pub['edition']: pub for pub in latest_publications(path)
            if pub['source_id'] == activity_id}


def answer_query(path, activity_id, store, level, current_edition='2', clock=_now):
    """客服核对：给出三态答复，并把结论与依据收据一并留痕。"""
    publications = publications_for(path, activity_id)
    observations_by_pub = {
        edition: _latest_observations(path, activity_id, edition)
        for edition in EDITIONS
    }
    verdict = evaluate(activity_id, store, level, current_edition,
                       publications, observations_by_pub)
    verdict['answer_id'] = record_answer(path, verdict, clock())
    verdict['reason_text'] = explain_reason(verdict['reason'])
    return verdict


def recheck_pending(path, activity_id, store, level_candidates, current_edition='2'):
    """列出仍待重新核实的会员等级，附最近一次有效发布线索。"""
    observations_by_pub = {
        edition: _latest_observations(path, activity_id, edition)
        for edition in EDITIONS
    }
    return pending_levels(activity_id, store, level_candidates, current_edition,
                          publications_for(path, activity_id), observations_by_pub)


def trace_answer(path, answer_id):
    """重启后追查旧答复：答复内容、依据收据原文、证据链完整性一并返回。"""
    answers = [answer for answer in list_answers(path) if answer['answer_id'] == answer_id]
    if not answers:
        return None
    answer = answers[0]
    answer['reason_text'] = explain_reason(answer['reason'])
    evidence = {}
    for label, receipt_map in (('basis', answer['basis_receipt_ids']),
                               ('context', answer.get('evidence_receipt_ids', {}))):
        for kind, receipt_id in receipt_map.items():
            observation = get_observation(path, receipt_id)
            evidence[label + ':' + kind] = {
                'receipt_id': receipt_id,
                'observation': observation} if observation else None
    return {'answer': answer, 'evidence': evidence,
            'chain_broken_receipts': verify_chain(path)}
