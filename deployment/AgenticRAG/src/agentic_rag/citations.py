"""Read confirmed source history and legacy saved citations from archives."""

import json
from uuid import UUID

from .domain import Citation
from .evidence import read_evidence
from .source_archive import contains, digest, encode, invalid


def open_citation(catalog, citation_id: UUID):
    """Read a saved citation or delivered evidence marker from source history."""
    with catalog._db.transaction() as connection:
        row=connection.execute('SELECT payload,run_id,evidence_id FROM saved_citations WHERE citation_id=?',(str(citation_id),)).fetchone()
    if row is None:
        with catalog._db.transaction() as connection:
            delivered=connection.execute('SELECT run_id FROM delivered_evidence WHERE evidence_id=?',
                                         (str(citation_id),)).fetchone()
        if delivered is None:
            raise invalid('historical citation not found', 'citation_history')
        evidence,source,_=read_evidence(catalog,UUID(delivered[0]),citation_id)
        return {'evidence':evidence.model_dump(mode='json'),'source_ref':evidence.source_ref.model_dump(mode='json'),
                'file_name':source.version.source_metadata.original_name,'source_uri':source.version.source_uri,
                'raw_hash':source.version.raw_hash,'parsed_hash':source.version.parsed_hash,
                'section_path':list(source.section(evidence.source_ref.section_id).heading_path),
                'quotes':[source.text[s.start:s.end] for s in evidence.spans],
                'lines':[list(source.chunks.parsed.source_map.lines(s)) for s in evidence.spans],
                'evidence_marker':'[^'+str(evidence.evidence_id)+']'}
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
