"""Standalone retrieval, using the existing layered CodePlus configuration."""

import argparse
from contextlib import closing
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys

from codeplus.config import load_config
from .service import KnowledgeService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local Markdown/PDF/DOCX knowledge retrieval (no answer model)")
    parser.add_argument("--config", type=Path, help="Existing CodePlus YAML configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create").add_argument("name")
    importer = commands.add_parser("import", aliases=["update"], help="Import or replace the document at this source path")
    importer.add_argument("kb_id")
    importer.add_argument("source", type=Path)
    search = commands.add_parser("search")
    search.add_argument("kb_id")
    search.add_argument("query")
    search.add_argument("--top-k", type=int)
    commands.add_parser("status").add_argument("kb_id")
    commands.add_parser("retry", help="Resume the registered pending operation").add_argument("kb_id")
    remove = commands.add_parser("remove")
    remove.add_argument("kb_id")
    remove.add_argument("doc_id")
    source = commands.add_parser("source", help="Read a saved chunk and its generation's original location")
    source.add_argument("kb_id")
    source.add_argument("chunk_id")
    args = parser.parse_args(argv)
    try:
        with closing(KnowledgeService(load_config(args.config).knowledge)) as service:
            if args.command == "create":
                result = service.create(args.name)
            elif args.command in {"import", "update"}:
                result = service.import_document(args.kb_id, args.source)
            elif args.command == "search":
                result = service.search(args.kb_id, args.query, args.top_k)
            elif args.command == "remove":
                result = service.remove(args.kb_id, args.doc_id)
            elif args.command == "retry":
                result = service.retry(args.kb_id)
            elif args.command == "source":
                result = service.source(args.kb_id, args.chunk_id)
            else:
                result = service.status(args.kb_id)
        print(json.dumps(asdict(result) if is_dataclass(result) else result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"knowledge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
