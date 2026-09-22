"""Exact archived quote validation and stable historical footnotes; no LLM IO."""

import json
import re
from uuid import UUID, uuid4

from .domain import Citation, Span
from .evidence import read_evidence
from .source_archive import contains, digest, encode, invalid


class CitationRegistry:
    def __init__(self, session):
        self.session=session

    def validate(self, evidence_id: UUID, spans: tuple[Span,...], quotes: tuple[str,...]):
        with self.session.catalog._db.transaction() as connection:
            self.session._active(connection)
        evidence,source,_=read_evidence(self.session.catalog,self.session.run.run_id,evidence_id)
        if (not spans or len(spans)!=len(quotes) or
                any(not isinstance(s,Span) for s in spans) or
                any(a.end>b.start for a,b in zip(spans,spans[1:]))):
            raise invalid('quotes require ordered, non-overlapping exact source spans', 'citation')
        for span,quote in zip(spans,quotes):
            location=f'evidence={evidence_id} span=[{span.start},{span.end})'
            if not contains(evidence.spans,span):
                raise invalid('citation_range_unconfirmed '+location, 'citation')
            if source.text[span.start:span.end]!=quote:
                raise invalid('citation_quote_mismatch '+location, 'citation')
        return evidence,source

    def save(self, evidence_id: UUID, spans: tuple[Span,...], quotes: tuple[str,...]):
        evidence,source=self.validate(evidence_id,spans,quotes)
        # Hash the structured sequence: neither an omitted gap nor a separator is
        # represented as continuous source. Individual exact excerpts are saved.
        quote_hash=digest(encode(list(quotes)))
        with self.session.catalog._db.transaction(write=True) as connection:
            self.session._active(connection)
            rows=connection.execute('SELECT payload FROM saved_citations WHERE run_id=? AND evidence_id=?',
                                    (str(self.session.run.run_id),str(evidence_id)))
            for row in rows:
                old=json.loads(row[0]);record=Citation.model_validate_json(encode(old['citation']))
                if record.spans==spans and record.quote_hash==quote_hash:
                    return old
            record=Citation(citation_id=uuid4(),run_id=self.session.run.run_id,evidence_id=evidence_id,
                            spans=spans,quote_hash=quote_hash)
            value={'citation':record.model_dump(mode='json'),'source_ref':evidence.source_ref.model_dump(mode='json'),
                   'file_name':source.version.source_metadata.original_name,'source_uri':source.version.source_uri,
                   'raw_hash':source.version.raw_hash,'parsed_hash':source.version.parsed_hash,
                   'section_path':list(source.section(evidence.source_ref.section_id).heading_path),
                   'quotes':list(quotes),'lines':[list(source.chunks.parsed.source_map.lines(s)) for s in spans],
                   'evidence_marker':'[^'+str(evidence_id)+']','citation_marker':'[^'+str(record.citation_id)+']'}
            connection.execute('INSERT INTO saved_citations VALUES(?,?,?,?)',
                (str(record.citation_id),str(record.run_id),str(record.evidence_id),encode(value)))
        return value

    def render_markdown(self, draft: str, saved: tuple[dict,...]):
        """Resolve every reserved draft evidence marker to a saved citation.

        Draft footnotes are structured claims saved by this registry first. The
        adapter does not concatenate the provisional tool marker with a different
        footnote ID. All [^...] syntax is reserved for citations in this core
        renderer, including within code examples; no heuristic semantic checking.
        """
        with self.session.catalog._db.transaction() as connection:
            self.session._active(connection)
        if not isinstance(draft,str) or not draft.strip():
            raise invalid('nonempty citation draft required','citation')
        if re.search(r'^\s*\[\^[^\]\r\n]+\]:',draft,re.MULTILINE):
            raise invalid('draft cannot supply its own source footnote definitions','citation')
        aliases={}
        for value in saved:
            record=Citation.model_validate_json(encode(value['citation']))
            actual=open_citation(self.session.catalog,record.citation_id)
            if actual!=value or record.run_id!=self.session.run.run_id:
                raise invalid('saved citation belongs to another run or was modified','citation')
            if value['evidence_marker'] in aliases:
                raise invalid('one draft evidence marker cannot refer to different citation claims','citation')
            aliases[value['evidence_marker']]=value
        markers=re.findall(r'\[\^[^\]\r\n]+\]',draft)
        if not markers or any(marker not in aliases for marker in markers) or set(markers)!=set(aliases):
            raise invalid('unknown, missing or unused citation marker','citation')
        body=re.sub(r'\[\^[^\]\r\n]+\]',lambda m:aliases[m.group()]['citation_marker'],draft)
        ordered=list(dict.fromkeys(markers))
        rendered=body+'\n\n'+'\n\n'.join(footnote(aliases[marker]) for marker in ordered)
        return {'markdown':rendered,'sha256':digest(rendered),
                'citation_ids':[aliases[marker]['citation']['citation_id'] for marker in ordered]}


def open_citation(catalog, citation_id: UUID):
    """User history read, not an active-run source capability or evidence grant."""
    with catalog._db.transaction() as connection:
        row=connection.execute('SELECT payload,run_id,evidence_id FROM saved_citations WHERE citation_id=?',(str(citation_id),)).fetchone()
    if row is None:
        raise invalid('historical citation not found', 'citation_history')
    value=json.loads(row[0])
    record=Citation.model_validate_json(encode(value['citation']))
    if record.citation_id!=citation_id or (str(record.run_id),str(record.evidence_id))!=(row[1],row[2]):
        raise invalid('historical citation row and payload identities differ','citation_history')
    evidence,source,_=read_evidence(catalog,record.run_id,record.evidence_id)
    if (evidence.source_ref.model_dump(mode='json')!=value['source_ref'] or
            source.version.raw_hash!=value['raw_hash'] or source.version.parsed_hash!=value['parsed_hash'] or
            digest(encode(value['quotes']))!=record.quote_hash or len(value['quotes'])!=len(record.spans)):
        raise invalid('historical citation binding differs', 'citation_history')
    expected={'file_name':source.version.source_metadata.original_name,'source_uri':source.version.source_uri,
              'section_path':list(source.section(evidence.source_ref.section_id).heading_path),
              'lines':[list(source.chunks.parsed.source_map.lines(s)) for s in record.spans],
              'evidence_marker':'[^'+str(record.evidence_id)+']','citation_marker':'[^'+str(record.citation_id)+']'}
    if any(value[key]!=actual for key,actual in expected.items()):
        raise invalid('historical citation display metadata differs from archived version/map','citation_history')
    for span,quote in zip(record.spans,value['quotes']):
        if not contains(evidence.spans,span) or source.text[span.start:span.end]!=quote:
            raise invalid('historical quote is not exact archived evidence', 'citation_history')
    return value


def footnote(value):
    """Stable Markdown metadata. Quote pieces are visibly separate excerpts."""
    record=Citation.model_validate_json(encode(value['citation']))
    name=value['file_name'].replace('\n',' ').replace('\r',' ').replace('[','\\[').replace(']','\\]')
    header=f"[^{record.citation_id}]: {name}; version {value['source_ref']['document_version_id']}; citation {record.citation_id}"
    pieces=[]
    for span,quote,lines in zip(record.spans,value['quotes'],value['lines']):
        pieces.append(f"    Excerpt [{span.start},{span.end}), lines {lines[0]}–{lines[1]}:\n"+
                      '\n'.join('    > '+line for line in quote.split('\n')))
    return header+'\n'+'\n\n'.join(pieces)
