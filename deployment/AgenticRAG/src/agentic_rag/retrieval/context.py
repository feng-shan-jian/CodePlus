"""Deterministic per-query selection; scores never cross query boundaries."""

from ..domain import Span
from ..source_archive import union


CONTEXT_POLICY = {'version':'query-relative-v1', 'parameter_status':'experiment',
    'relative_score_band':0.02, 'source_preference':'unselected_document_within_band',
    'overlap':'same_document_version_current_window_and_this_result',
    'crop':'halve_canonical_range_until_complete_serialization_fits'}


def complementary_order(hits):
    """Relevance bands anchor on their first score, without chained drift.

    Preserve retrieval rank within a source preference. Legacy Dense adapters
    that omit scores keep their original order.
    """
    pending = list(hits)
    documents = set()
    while pending:
        anchor = pending[0].get('score')
        stop = 1
        if anchor is not None:
            tolerance = abs(anchor) * CONTEXT_POLICY['relative_score_band']
            while stop < len(pending) and pending[stop].get('score') is not None and abs(anchor-pending[stop]['score']) <= tolerance:
                stop += 1
        band, pending = pending[:stop], pending[stop:]
        while band:
            index = next((i for i,hit in enumerate(band) if hit['document_id'] not in documents), 0)
            hit = band.pop(index)
            documents.add(hit['document_id'])
            yield hit


def subtract(span, covered):
    """Exact Unicode-codepoint subtraction; retains disjoint same-source facts."""
    start, result = span.start, []
    for old in union(covered):
        if old.end <= start or old.start >= span.end:
            continue
        if old.start > start:
            result.append(Span(start=start, end=min(old.start, span.end)))
        start = max(start, old.end)
        if start >= span.end:
            break
    if start < span.end:
        result.append(Span(start=start, end=span.end))
    return result


def window_removed(previous, current):
    """Trace removed locations independently of candidate/evidence identities."""
    coverage = {}
    for item in current:
        coverage.setdefault(item['source_ref']['document_version_id'], []).append(Span.model_validate(item['source_span']))
    return [{'candidate_id':item['candidate_id'], 'evidence_id':item['evidence_id'],
             'source_ref':item['source_ref'], 'source_span':span.model_dump()} for item in previous
        for span in subtract(Span.model_validate(item['source_span']), coverage.get(item['source_ref']['document_version_id'], []))]
