"""Shared retrieval rules without model, database or experiment dependencies."""

ANALYZER = {"tokenizer": {"type": "jieba", "mode": "search", "hmm": False}, "filter": ["lowercase"]}
BM25_INDEX_PARAMS = {"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75}


def rrf(dense, bm25, constant):
    """Equal weights, one-based ranks, deterministic chunk-ID tie break; preserve both scores."""
    merged = {}
    for lane, hits in (("dense", dense), ("bm25", bm25)):
        for rank, hit in enumerate(hits, 1):
            entry = merged.setdefault(hit["chunk_id"], {
                **hit, "score": 0.0, "score_type": "rrf", "dense_rank": None, "dense_score": None,
                "bm25_rank": None, "bm25_score": None,
            })
            entry[f"{lane}_rank"], entry[f"{lane}_score"] = rank, hit["score"]
            entry["score"] += 1 / (constant + rank)
    result = sorted(merged.values(), key=lambda hit: (-hit["score"], hit["chunk_id"]))
    return [{**hit, "rank": rank} for rank, hit in enumerate(result, 1)]
