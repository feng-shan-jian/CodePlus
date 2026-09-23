"""Equal-weight reciprocal rank fusion over stable chunk identities."""


def reciprocal_rank_fusion(branches, k):
    """Keep each branch's first occurrence and its original one-based rank.

    Scores on different scales are deliberately unused. The full ordering is
    returned for diagnostics; the caller applies the final candidate limit.
    """
    if type(k) is not int or k < 1:
        raise ValueError('positive integer rrf_k required')
    scores, ranks = {}, {}
    for branch, candidates in branches.items():
        seen = set()
        for rank, candidate in enumerate(candidates, 1):
            identity = candidate['chunk_id']
            if identity in seen:
                continue
            seen.add(identity)
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(identity, {})[branch] = rank
    return [{'chunk_id':identity, 'rank':rank, 'score':scores[identity],
             'score_type':'rrf', 'branch_ranks':ranks[identity]}
            for rank, identity in enumerate(sorted(scores, key=lambda i: (-scores[i], i)), 1)]
