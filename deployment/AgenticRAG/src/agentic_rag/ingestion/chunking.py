"""Contiguous canonical spans, with structural/sentence/token boundary preference."""

from bisect import bisect_left, bisect_right
import hashlib
import re
import unicodedata
from uuid import uuid5

from .._schema import NonNegativeInt, PositiveInt, Record, fingerprint
from ..config import ChunkerConfig
from ..domain import Chunk, ErrorCode, RagError, Span
from .parsing import ParsedDocument

CHUNKER_IMPLEMENTATION = 'structure_sentence_token'
CHUNKER_VERSION = 'canonical-offsets-v1'


def chunker_config(max_tokens=512, overlap_tokens=64):
    """Experiment start, not R23 quality-frozen defaults. max includes template/EOS."""
    return ChunkerConfig(implementation=CHUNKER_IMPLEMENTATION, version=CHUNKER_VERSION,
                         max_tokens=max_tokens, overlap_tokens=overlap_tokens)


def chunker_fingerprint(config, profile):
    return fingerprint('chunker', {'config': config.model_dump(mode='json'), 'embedding': profile.identity})


class ChunkInput(Record):
    chunk: Chunk
    index_title: str
    complete_embedding_tokens: PositiveInt
    overlap_codepoints: NonNegativeInt
    overlap_tokens: NonNegativeInt
    # These notes are metadata and never inserted into evidence or model input.
    split_structures: tuple[str, ...] = ()


class ChunkSet(Record):
    parsed: ParsedDocument
    inputs: tuple[ChunkInput, ...]


def _fit(text, start, end, frontier, title, tokenizer, limit):
    remaining = text[start:end]
    offsets = tokenizer.offsets(remaining)
    # NFC gaps are absorbed by the continuous cursor; duplicate emoji offsets
    # disappear only as candidate boundaries, never as evidence characters.
    cuts = set()
    for _, stop in offsets:
        stop += start
        while stop < end and (unicodedata.category(text[stop]).startswith('M') or text[stop] == '\u200d'):
            stop += 1
        if frontier < stop <= end:
            cuts.add(stop)
    cuts.add(end)
    candidates = sorted(cuts)
    low, high, best = 0, len(candidates) - 1, None
    # BPE counts need not be strictly monotone. Binary search is a conservative
    # fitting heuristic, not a claim of maximal packing; every accepted cut is
    # independently re-encoded, so non-monotonicity cannot violate the budget.
    while low <= high:
        mid = (low + high) // 2
        candidate = candidates[mid]
        if tokenizer.document(text[start:candidate], title, check=False).token_count <= limit:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        # A single offset/grapheme can be bigger than the configured limit.
        # Preserve original codepoints even in this last-resort boundary split.
        low, high = frontier + 1, candidates[0]
        while low <= high:
            mid = (low + high) // 2
            if tokenizer.document(text[start:mid], title, check=False).token_count <= limit:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
    return best


def _overlap_start(text, start, end, tokenizer, budget):
    if not budget:
        return end
    offsets = tokenizer.offsets(text[start:end])
    candidates = sorted({start + a for a, _ in offsets if start < start + a < end})
    # Exact re-encoding includes NFC/ByteLevel effects at the new beginning.
    for point in candidates[max(0, len(candidates) - budget - 2):]:
        if len(tokenizer.encode(text[point:end])) <= budget:
            return point
    return end


def chunk_document(parsed: ParsedDocument, config: ChunkerConfig, tokenizer) -> ChunkSet:
    if config.implementation != CHUNKER_IMPLEMENTATION or config.version != CHUNKER_VERSION:
        raise RagError(ErrorCode.IDENTITY_MISMATCH, 'chunker identity is unavailable', stage='chunk')
    limit = min(config.max_tokens, tokenizer.profile.limits.max_input_tokens)
    identity = chunker_fingerprint(config, tokenizer.profile)
    inputs = []
    navigation = {p for b in parsed.blocks if b.kind == 'heading' for p in range(b.span.start, b.span.end)}
    # Precompute once per document. Token.map starts at a line, which may have
    # arbitrarily much indentation before its first actual content codepoint.
    required_positions = [p for p, char in enumerate(parsed.text) if not char.isspace() and p not in navigation]
    for section in parsed.sections:
        if section.span not in parsed.eligible_spans:
            continue
        title = ' / '.join(section.heading_path)
        if tokenizer.document('', title, check=False).token_count >= limit:
            raise RagError(ErrorCode.INPUT_TOO_LONG, f'title leaves no body budget within {limit} complete tokens', stage='chunk')
        structural = sorted({b.span.end for b in parsed.blocks if b.level == 0 and b.kind != 'heading' and
                             section.span.start < b.span.end <= section.span.end})
        first = bisect_left(required_positions, section.span.start)
        body_start = required_positions[first] if first < len(required_positions) else section.span.end
        sentences = [section.span.start + m.end() for m in re.finditer(
            r'(?<=[。！？])|(?<=[.!?])(?:[ \t]+|\n+)|\n{2,}', parsed.text[section.span.start:section.span.end])]
        start = frontier = previous_chunk_end = section.span.start
        while frontier < section.span.end:
            end = _fit(parsed.text, start, section.span.end, frontier, title, tokenizer, limit)
            if end is None and start < frontier:
                # Drop overlap if it would prevent new evidence from progressing.
                start = frontier
                end = _fit(parsed.text, start, section.span.end, frontier, title, tokenizer, limit)
            if end is None:
                raise RagError(ErrorCode.INPUT_TOO_LONG, 'one source codepoint plus title exceeds complete input budget', stage='chunk')
            if end != section.span.end:
                for boundaries in (structural, sentences):
                    index = bisect_right(boundaries, end) - 1
                    if index >= 0 and boundaries[index] > max(frontier, body_start):
                        candidate = boundaries[index]
                        if tokenizer.document(parsed.text[start:candidate], title, check=False).token_count <= limit:
                            end = candidate
                            break
            body = parsed.text[start:end]
            next_required = bisect_left(required_positions, start)
            required_start = required_positions[next_required] if next_required < len(required_positions) else section.span.end
            if required_start >= end:
                # Canonical whitespace remains archived. A model input requires
                # actual content; coverage independently authenticates and lists
                # every omitted whitespace range instead of hiding a gap.
                start = frontier = min(required_start, section.span.end)
                continue
            count = tokenizer.document(body, title).token_count
            if count > limit:
                raise RagError(ErrorCode.INPUT_TOO_LONG, 'complete chunk exceeds configured budget', stage='chunk')
            span = Span(start=start, end=end)
            chunk = Chunk(chunk_id=uuid5(section.document_version_id, f'chunk:{identity}:{start}:{end}'),
                          document_version_id=section.document_version_id, section_id=section.section_id,
                          spans=(span,), text_hash=hashlib.sha256(body.encode('utf-8')).hexdigest(),
                          chunker_fingerprint=identity)
            split = tuple(sorted({b.kind for b in parsed.blocks if b.kind in {'fence', 'code_block', 'table', 'bullet_list', 'ordered_list'}
                                  and b.span.start < end and b.span.end > start and
                                  (b.span.start < start or b.span.end > end)}))
            inputs.append(ChunkInput(chunk=chunk, index_title=title, complete_embedding_tokens=count,
                overlap_codepoints=max(0, previous_chunk_end - start),
                overlap_tokens=len(tokenizer.encode(parsed.text[start:max(start, previous_chunk_end)])),
                split_structures=split))
            previous_chunk_end = end
            frontier = end
            start = _overlap_start(parsed.text, start, end, tokenizer, config.overlap_tokens)
    result = ChunkSet(parsed=parsed, inputs=tuple(inputs))
    coverage(result)  # independent eligible spans were derived before chunking
    return result


def coverage(result: ChunkSet) -> dict:
    """Validate partitioned sections, exact spans/hashes and coverage denominator."""
    parsed = result.parsed
    length, total, covered, overlap = len(parsed.text), 0, 0, 0
    if parsed.sections and (parsed.sections[0].span.start != 0 or parsed.sections[-1].span.end != length or
            any(a.span.end != b.span.start for a, b in zip(parsed.sections, parsed.sections[1:]))):
        raise ValueError('sections do not partition canonical text')
    if not parsed.sections and length:
        raise ValueError('nonempty text requires sections')
    gaps, unions, excluded = [], [], []
    known = {s.section_id: s for s in parsed.sections}
    navigation = {p for b in parsed.blocks if b.kind == 'heading' for p in range(b.span.start, b.span.end)}
    for item in result.inputs:
        chunk = item.chunk
        section = known.get(chunk.section_id)
        if (section is None or chunk.document_version_id != section.document_version_id or
                len(chunk.spans) != 1 or section.span not in parsed.eligible_spans):
            raise ValueError('chunk has invalid section/version/eligibility')
        span = chunk.spans[0]
        if not section.span.start <= span.start < span.end <= section.span.end:
            raise ValueError('chunk outside own section')
        if hashlib.sha256(parsed.text[span.start:span.end].encode()).hexdigest() != chunk.text_hash:
            raise ValueError('chunk body hash mismatch')
        if not any(not parsed.text[p].isspace() and p not in navigation for p in range(span.start, span.end)):
            raise ValueError('empty model input body')
    for section in parsed.sections:
        if section.span not in parsed.eligible_spans:
            excluded.append({'span': section.span.model_dump(), 'reason': 'no_body'})
            continue
        cursor = section.span.start
        merged = []
        spans = sorted((i.chunk.spans[0] for i in result.inputs if i.chunk.section_id == section.section_id), key=lambda s: (s.start, s.end))
        for span in spans:
            total += span.end - span.start
            if span.start > cursor:
                gaps.append({'start': cursor, 'end': span.start})
            if merged and span.start <= merged[-1]['end']:
                merged[-1]['end'] = max(merged[-1]['end'], span.end)
            else:
                merged.append({'start': span.start, 'end': span.end})
            overlap += max(0, min(cursor, span.end) - span.start)
            covered += max(0, span.end - max(cursor, span.start))
            cursor = max(cursor, span.end)
        if cursor != section.span.end:
            gaps.append({'start': cursor, 'end': section.span.end})
        unions.extend(merged)
    missing_body = [g for g in gaps if any(not parsed.text[p].isspace() and p not in navigation
                                          for p in range(g['start'], g['end']))]
    if missing_body:
        raise ValueError(f'canonical evidence coverage gaps: {missing_body}')
    for gap in gaps:
        start = gap['start']
        while start < gap['end']:
            reason = 'heading_navigation' if start in navigation else 'whitespace'
            end = start + 1
            while end < gap['end'] and (end in navigation) == (reason == 'heading_navigation'):
                end += 1
            excluded.append({'span': {'start': start, 'end': end}, 'reason': reason})
            start = end
    eligible = sum(s.end - s.start for s in parsed.eligible_spans)
    omitted = sum(g['end'] - g['start'] for g in gaps)
    if covered + omitted != eligible or total - covered != overlap:
        raise ValueError('coverage arithmetic differs')
    required = sum(not parsed.text[p].isspace() and p not in navigation
                   for s in parsed.eligible_spans for p in range(s.start, s.end))
    whitespace = sum(e['span']['end'] - e['span']['start'] for e in excluded if e['reason'] == 'whitespace')
    return {'canonical_codepoints': length, 'eligible_codepoints': eligible,
            'eligible_spans': [s.model_dump() for s in parsed.eligible_spans], 'excluded_spans': excluded,
            'covered_codepoints': covered, 'union_spans': unions, 'gap_spans': missing_body,
            'required_nonwhitespace_codepoints': required, 'covered_required_codepoints': required,
            'omitted_whitespace_codepoints': whitespace, 'omitted_navigation_codepoints': omitted - whitespace,
            'overlap_codepoints': overlap, 'chunk_codepoints': total,
            'status': 'indexed_body' if result.inputs else 'no_body'}
