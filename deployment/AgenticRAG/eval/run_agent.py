"""Run query-only evaluation inputs through the ordinary CodePlus Agent.

Answers use eval/RAG-eval/score.py in a separate process. This runner does not
load gold, rubrics, reference answers, or an answer-validation policy.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from uuid import UUID

from runtime_inputs import load_runtime_inputs


async def run(args):
    from agentic_rag.adapters.codeplus.policy import KnowledgePolicy
    from agentic_rag.config import DevelopmentConfig
    from codeplus.agent import Agent
    from codeplus.client import create_client
    from codeplus.config import load_config
    from codeplus.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
    from codeplus.tools import create_default_registry

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = DevelopmentConfig.model_validate_json(args.settings.read_text(encoding="utf-8"))
    state = json.loads(args.state.read_text(encoding="utf-8"))
    ids = json.loads(Path(__file__).with_name("development-ids.json").read_text(encoding="utf-8"))
    questions = load_runtime_inputs(question_ids=ids)["questions"]
    # The same query-only boundary as the retrieval runner; install it before
    # any Agent tools can open the evaluation's scoring-side files.
    from dense_runner import protect_runtime_reads
    protect_runtime_reads()
    provider = load_config().providers[0]
    records = [{**question, "status": "error", "not_started": True, "model_answer": ""} for question in questions]
    (output / "plan.json").write_text(json.dumps({"split": "development", "mode": args.mode,
        "questions": questions, "settings": settings.model_dump(mode="json")}, ensure_ascii=False, indent=2), encoding="utf-8")
    for number, question in enumerate(questions):
        if args.limit is not None and number >= args.limit:
            break
        policy = KnowledgePolicy(settings, UUID(state["kb_id"]), provider, mode=args.mode)
        client = create_client(provider)
        checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(output)), RuleEngine(),
                                    mode=PermissionMode.ACCEPT_EDITS)
        agent = Agent(client, create_default_registry(), provider.protocol, work_dir=str(output),
                      context_window=provider.get_context_window(),
                      execution_policy=policy, permission_checker=checker)
        record = {**question, "status": "error", "model_answer": ""}
        stop = False
        try:
            record["model_answer"] = await agent.run_to_completion(question["query"])
            outcome = agent.last_run_outcome
            record["outcome"] = asdict(outcome) if outcome else None
            record["status"] = "ok" if outcome and outcome.status == "completed" else "error"
        except Exception as exc:
            record["error"] = {"type": type(exc).__name__, "message": str(exc)}
            stop = getattr(exc.__cause__, "status_code", None) == 402
        finally:
            await client._client.close()
            if policy.scope is not None:
                scope = policy.scope
                with scope.catalog._db.transaction() as db:
                    host = db.execute("SELECT detail FROM host_runs WHERE run_id=?", (scope.run_id,)).fetchone()
                    record["delivery"] = json.loads(host[0]) if host else None
                    record["source_receipts"] = [list(row) for row in db.execute(
                        "SELECT * FROM delivery_receipts WHERE run_id=?", (scope.run_id,))]
            records[number] = record
            (output / "answers.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"id": question["id"], "status": record["status"]}), flush=True)
        if stop:
            break
    return 0 if all(record["status"] == "ok" for record in records) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("settings", "state", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mode", choices=("fixed", "auto"), default="auto")
    parser.add_argument("--limit", type=int, help="Pilot only; unstarted rows stay in the result")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
