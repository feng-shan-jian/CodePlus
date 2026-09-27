import pytest
from score_evidence import covered_sentences,merge_intervals,map_sentence_spans


def test_complete_alternative_not_all_alternatives_is_required():
    rationale={"sentences":{"0":[0,10],"1":[20,30],"2":[40,50]},"alternatives":[[0,1],[2]]}
    assert covered_sentences(rationale,[[0,10]])==(False,[0])
    assert covered_sentences(rationale,[[40,50]])==(True,[2])
    assert covered_sentences(rationale,[[0,10],[20,30]])==(True,[0,1])


def test_partial_sentence_does_not_count_but_adjacent_chunk_union_can_complete_it():
    rationale={"sentences":{"0":[10,30]},"alternatives":[[0]]}
    assert covered_sentences(rationale,[[10,20]])==(False,[])
    assert covered_sentences(rationale,[[10,20],[20,30]])==(True,[0])
    assert covered_sentences(rationale,[[10,19],[20,30]])==(False,[])


def test_intervals_merge_overlap_and_reject_invalid_ranges():
    assert merge_intervals([[20,30],[0,10],[5,25]])==[[0,30]]
    assert merge_intervals([])==[]
    with pytest.raises(ValueError):
        merge_intervals([[10,10]])


def test_sentence_alignment_allows_only_whitespace_formatting():
    assert map_sentence_spans([" a b. \n"," C d."],"a  b.\nC d.",10)=={0:[10,15],1:[16,20]}
    with pytest.raises(ValueError,match="tokens differ"):
        map_sentence_spans(["a b."],"a not b.",0)
    with pytest.raises(ValueError,match="Unmatched"):
        map_sentence_spans(["a b."],"a b. extra",0)
