"""Independent R03 AutoTokenizer oracle, no model load or GPU computation."""
import json
from pathlib import Path
import sys
import importlib.metadata as metadata
from transformers import AutoTokenizer

cache, request = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
response = []
for case in request:
    tokenizer = AutoTokenizer.from_pretrained(cache/case['model']/case['revision'],
                padding_side='left', local_files_only=True, trust_remote_code=False)
    response.append({'ids':[token for piece in case['pieces'] for token in tokenizer.encode(
        piece, add_special_tokens=case['special'], truncation=False)],
        'offsets': tokenizer(case['sample'], add_special_tokens=False,return_offsets_mapping=True)['offset_mapping']})
print(json.dumps({'results':response, 'python':sys.executable,
                  'versions':{p:metadata.version(p) for p in ('transformers','tokenizers')}},ensure_ascii=True))
