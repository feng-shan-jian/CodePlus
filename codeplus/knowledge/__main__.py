"""Standalone retrieval, using the existing layered CodePlus configuration."""

import argparse
from contextlib import closing, redirect_stdout
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys

from codeplus.config import KnowledgeConfig, load_config
from .service import KnowledgeService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local Markdown/PDF/DOCX knowledge retrieval (no answer model)")
    parser.add_argument("--config", type=Path, help="Existing CodePlus YAML configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Prepare services/model, or retry failed environment preparation")
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
    evaluate = commands.add_parser("evaluate", help="Frozen synthetic Milvus experiment; never migrates a daily base")
    inputs = evaluate.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--fixtures", type=Path, help="Directory containing Markdown originals and questions.json")
    inputs.add_argument("--replay", type=Path, help="Use this frozen.json without loading an embedding model or fixtures")
    evaluate.add_argument("--mode", choices=("all", "dense", "bm25", "hybrid"), default="all")
    evaluate.add_argument("--top-k", type=int, default=3)
    evaluate.add_argument("--candidates", type=int, default=6, help="Candidates per lane; also used by ANN comparisons")
    evaluate.add_argument("--rrf-k", type=float, default=KnowledgeConfig.rrf_k)
    evaluate.add_argument("--m", type=int, default=16)
    evaluate.add_argument("--ef-construction", type=int, default=128)
    evaluate.add_argument("--ef", type=int, nargs="+", default=[16, 64], help="Two or more query-only ef settings")
    evaluate.add_argument("--warmup", type=int, default=1, help="Warmup rounds over all questions, excluded from percentiles")
    evaluate.add_argument("--repeats", type=int, default=3)
    benchmark = commands.add_parser("benchmark", help="Run or replay a versioned local corpus regression dataset")
    benchmark.add_argument("--dataset", type=Path, required=True)
    benchmark_inputs = benchmark.add_mutually_exclusive_group(required=True)
    benchmark_inputs.add_argument("--kb-id", help="Existing base containing exactly the dataset source versions")
    benchmark_inputs.add_argument("--replay", type=Path, help="Rescore report.json without Milvus or an embedding model")
    benchmark_inputs.add_argument("--check", action="store_true", help="Check corpus fingerprints and reference quotes only")
    benchmark.add_argument("--managed-local", action="store_true", help="Prepare the project-managed local Milvus deployment")
    benchmark.add_argument("--mode", choices=("dense", "bm25", "hybrid"), help="Override retrieval mode for this run only")
    benchmark.add_argument("--candidates", type=int, help="Override candidates per hybrid lane (1-16384)")
    benchmark.add_argument("--rrf-k", type=float, help="Override the positive RRF constant for this run only")
    args = parser.parse_args(argv)
    try:
        if args.command == "benchmark":
            from .benchmark import run

            config = load_config(args.config).knowledge if args.kb_id else None
            result = run(config, args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "ok" else 1
        if args.command == "evaluate":
            from .evaluate import run

            # Experiments need no answer provider configuration. Explicit YAML still uses the existing loader.
            config = load_config(args.config).knowledge if args.config else KnowledgeConfig(enabled=True)
            result = run(config, args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "ok" else 1
        with closing(KnowledgeService(load_config(args.config).knowledge)) as service:
            if args.command not in {"status", "source"}:
                with redirect_stdout(sys.stderr):
                    service.prepare(lambda message: print(message, file=sys.stderr, flush=True))
            if args.command == "prepare":
                result = {"status": "ready"}
            elif args.command == "create":
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
