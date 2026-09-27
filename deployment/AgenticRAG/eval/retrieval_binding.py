"""Offline identity checks shared by native retrieval diagnostic scorers."""
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def bind(report, official, raw, all_questions, source_hash):
    """Bind exact bytes, source dataset, original ordered ID/query and denominators.

    No metric is changed and no medium-only count is built into the scorer.
    The official wrapper must already have scored these same report bytes.
    """
    if official.get('task') != 'retrieval' or official.get('input_sha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('official retrieval score does not belong to the exact input bytes')
    records = report['records']
    ids = [r['id'] for r in records]
    selected = set(ids)
    questions = [q for q in all_questions if q['id'] in selected]
    if [(q['id'], q['query']) for q in questions] != [(r['id'], r['query']) for r in records]:
        raise ValueError('original ordered question ID/query binding differs')
    expected = source_hash if len(questions) == len(all_questions) else fingerprint({
        'source_dataset_sha256': source_hash, 'total_questions': len(all_questions), 'question_ids': ids})
    if report['dataset_sha256'] != expected or official['dataset_sha256'] != expected:
        raise ValueError('dataset/selection fingerprint differs')
    positive = sum(bool(q['gold']) for q in questions)
    if (len(questions) != official['selected_questions'] or positive != official['scored_questions']
            or len(questions)-positive != official['excluded_null_queries'] or report['protocol']['top_k'] != 10):
        raise ValueError('official Top10/denominators differ')
    if any(r['status'] not in {'ok', 'error'} or (r['status'] == 'error' and r['hits']) for r in records):
        raise ValueError('failed requests must retain empty hits')
    return questions
