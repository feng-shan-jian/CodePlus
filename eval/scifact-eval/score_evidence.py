"""Offline complete-sentence evidence coverage on frozen retrieval outputs.

The 505-question annotated development denominator and alternative evidence-set
rule are unchanged. This does not predict support/refute labels or grade answers.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import scifact_suite as suite

SOURCE = suite.ROOT / "runs/annotations/scifact-20260927"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    """Verify the retained, frozen annotation mapping; never rewrite gold."""
    alignment = suite.read_json(SOURCE / "alignment.json")
    if file_hash(SOURCE / "data.tar.gz") != alignment["archive_sha256"]:
        raise ValueError("Original SciFact archive differs")
    if file_hash(SOURCE / "evidence-map.json") != alignment["evidence_map_sha256"]:
        raise ValueError("Evidence mapping changed")
    if file_hash(suite.ROOT / "runtime/development.json") != alignment["runtime_sha256"]:
        raise ValueError("Query input changed")
    return alignment


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if start<0 or end<=start:
            raise ValueError("Invalid evidence interval")
        if merged and start<=merged[-1][1]:
            merged[-1][1]=max(merged[-1][1],end)
        else:
            merged.append([start,end])
    return merged


def covered_sentences(rationale,intervals):
    merged=merge_intervals(intervals)
    covered={int(index) for index,(start,end) in rationale["sentences"].items()
        if any(left<=start and right>=end for left,right in merged)}
    complete=any(set(alternative)<=covered for alternative in rationale["alternatives"])
    return complete,sorted(covered)


def map_sentence_spans(sentences,text,offset):
    """Exact token alignment; allow whitespace formatting, never word changes."""
    tokens=list(re.finditer(r"\S+",text))
    position=0
    spans={}
    for index,sentence in enumerate(sentences):
        words=sentence.split()
        actual=tokens[position:position+len(words)]
        if [m.group() for m in actual]!=words:
            raise ValueError(f"Original sentence tokens differ at sentence {index}")
        if actual:
            spans[index]=[offset+actual[0].start(),offset+actual[-1].end()]
        else:
            end=tokens[position-1].end() if position else 0
            spans[index]=[offset+end,offset+end]
        position+=len(words)
    if position!=len(tokens):
        raise ValueError("Unmatched abstract tokens")
    return spans


def clusters(questions,qrels):
    """Queries sharing a labeled document belong to the same resampling block."""
    parents=list(range(len(questions)))
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]]
            i=parents[i]
        return i
    owners={}
    for i,q in enumerate(questions):
        for doc in qrels[q["id"]]:
            if doc in owners:
                parents[find(i)]=find(owners[doc])
            else:
                owners[doc]=i
    groups=defaultdict(list)
    for i in range(len(questions)):
        groups[find(i)].append(i)
    return list(groups.values())


def paired(before,after,questions,groups):
    delta=np.asarray([len(after[q["id"]]["covered"])/after[q["id"]]["required"]-
        len(before[q["id"]]["covered"])/before[q["id"]]["required"] for q in questions])
    sums=np.asarray([sum(delta[group]) for group in groups])
    sizes=np.asarray([len(group) for group in groups])
    rng=np.random.default_rng(260927)
    draws=rng.integers(0,len(groups),size=(10000,len(groups)))
    estimates=sums[draws].sum(axis=1)/sizes[draws].sum(axis=1)
    gains,losses,improved,regressed=0,0,0,0
    for q in questions:
        old,new=set(before[q["id"]]["covered"]),set(after[q["id"]]["covered"])
        gains+=len(new-old)
        losses+=len(old-new)
        improved+=len(new)>len(old)
        regressed+=len(new)<len(old)
    return {"macro_recall_delta":float(delta.mean()),"cluster_bootstrap_95":np.quantile(estimates,[.025,.975]).tolist(),
        "gained_qrels":gains,"lost_qrels":losses,"improved_questions":improved,"regressed_questions":regressed}


def score(run_name, baseline=None):
    directory = suite.ROOT / "runs/development" / run_name
    summary = suite.read_json(directory / "summary.json")
    alignment = prepare()
    runtime = suite.read_json(suite.ROOT / "runtime/development.json")
    all_questions = runtime["questions"]
    gold = suite.read_json(SOURCE / "evidence-map.json")
    if set(gold) != {q["id"] for q in all_questions}:
        raise ValueError("Missing evidence annotations")
    questions = [q for q in all_questions if gold[q["id"]]]
    groups = clusters(questions, gold)
    result = {"questions": len(questions), "full_development_questions": len(all_questions),
        "alignment": alignment, "answer_generated": False,
        "main_summary_sha256": file_hash(directory / "summary.json"),
        "scorer_sha256": file_hash(Path(__file__)), "arms": {}}
    details = {}
    for stage in ("rrf24", "rerank10", "context"):
        rows = suite.read_json(directory / f"{stage}.json")["records"]
        if len(rows) != len(all_questions):
            raise ValueError("Incomplete ranking")
        metrics = {}
        for k in (5, 10):
            detail, partial, document_recall = {}, 0, 0
            for question, row in zip(all_questions, rows, strict=True):
                qid = question["id"]
                if row["id"] != qid or row["query"] != question["query"]:
                    raise ValueError("Changed ranking query")
                if not gold[qid]:
                    continue
                intervals = {}
                for hit in row["hits"][:k]:
                    intervals.setdefault(hit["doc_id"], []).extend(
                        [span["char_start"], span["char_end"]] for span in hit["source_spans"])
                document_recall += len(set(intervals) & set(gold[qid])) / len(gold[qid])
                covered, sentences = [], {}
                for doc_id, rationale in gold[qid].items():
                    complete, found = covered_sentences(rationale, intervals.get(doc_id, []))
                    sentences[doc_id] = found
                    if complete:
                        covered.append(doc_id)
                    elif doc_id in intervals:
                        partial += 1
                detail[qid] = {"covered": covered, "required": len(gold[qid]), "sentences": sentences}
            count = len(questions)
            metrics[str(k)] = {
                "macro_complete_rationale_recall": sum(len(d["covered"]) / d["required"] for d in detail.values()) / count,
                "document_recall_same_annotated_stratum": document_recall / count,
                "complete_rationale_relations": sum(len(d["covered"]) for d in detail.values()),
                "any_complete_rationale_hit": sum(bool(d["covered"]) for d in detail.values()) / count,
                "fully_covered_questions": sum(len(d["covered"]) == d["required"] for d in detail.values()),
                "retrieved_gold_documents_missing_complete_rationale": partial}
            details[f"{stage}@{k}"] = detail
        result["arms"][stage] = {"label": stage, "windows": metrics}
    if baseline:
        previous = suite.read_json(baseline)
        result["baseline_sha256"] = file_hash(baseline)
        for stage, entry in result["arms"].items():
            entry["versus_selected_rrf10"] = {str(k): paired(previous[f"rrf10@{k}"], details[f"{stage}@{k}"], questions, groups) for k in (5, 10)}
    for name, value in (("evidence-summary.json", result), ("evidence-details.json", details)):
        with (directory / name).open("xb") as stream:
            stream.write(suite.encode(value))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "score"))
    parser.add_argument("--run-name", default="production-20260927")
    parser.add_argument("--baseline", type=Path, help="Frozen selected RRF10 evidence-details.json for paired comparison")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name):
        raise ValueError("Unsafe run name")
    if args.action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=False))
    else:
        score(args.run_name, args.baseline)
