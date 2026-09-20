"""One turn's evidence, distinct from conversation history and personal memory."""

import asyncio
from dataclasses import asdict
import json
import re

from codeplus.config import KnowledgeConfig


CITATIONS = re.compile(r"\[K:([^\]\n]*)\]")
READ_CHARS = 6000


class KnowledgeContext:
    def __init__(self, config: KnowledgeConfig):
        self.config = config
        self._service = None
        self.binding = None
        self.evidence = {}
        self.revision = None
        self.searches = 0
        self.report_error = ""
        self.retrieving = False
        self._prepare_task = None
        self._closed = False

    @property
    def service(self):
        if self._closed:
            raise RuntimeError("Knowledge context is closed")
        if not self.config.enabled:
            raise ValueError("Knowledge is disabled in configuration")
        if self._service is None:
            from .service import KnowledgeService
            self._service = KnowledgeService(self.config)
        return self._service

    @property
    def preparing(self):
        return self._prepare_task is not None and not self._prepare_task.done()

    async def prepare(self, progress=None):
        if self._closed:
            raise RuntimeError("Knowledge context is closed")
        if not self.preparing:
            loop = asyncio.get_running_loop()
            def report(message):
                if progress is not None:
                    loop.call_soon_threadsafe(progress, message)
            # A cancelled waiter must not abandon an in-flight model load / Compose call.
            self._prepare_task = asyncio.create_task(asyncio.to_thread(lambda: self.service.prepare(report)))
        await asyncio.shield(self._prepare_task)

    async def aclose(self):
        self._closed = True
        if self._prepare_task is not None:
            await asyncio.gather(asyncio.shield(self._prepare_task), return_exceptions=True)
        if self._service is not None:
            await asyncio.to_thread(self._service.close)

    def bind(self, binding):
        if binding is not None:
            if not isinstance(binding, dict) or not isinstance(binding.get("kb_id"), str):
                raise ValueError("Invalid knowledge binding")
            top_k = binding.get("top_k", self.config.top_k)
            if type(top_k) is not int or not 1 <= top_k <= 10:
                raise ValueError("Knowledge top_k must be between 1 and 10")
            binding = {"kb_id": binding["kb_id"], "top_k": top_k}
        self.binding = binding
        self.begin_turn()

    async def check(self):
        if self.binding is None:
            raise ValueError("No knowledge base selected; use /knowledge use <id>")
        if self._prepare_task is None:
            await self.prepare()
        else:
            await asyncio.shield(self._prepare_task)
        def check():
            with self.service._locked(self.binding["kb_id"]) as kb:
                if self.revision is not None and kb["revision"] != self.revision:
                    raise ValueError("Knowledge corpus changed; please regenerate the answer/report")
                return kb
        return await asyncio.to_thread(check)

    def begin_turn(self):
        self.evidence = {}
        self.revision = None
        self.searches = 0
        self.report_error = ""

    def _offer(self, source):
        chunk_id = source.get("chunk_id", source.get("id"))
        citation = f"K:{self.binding['kb_id']}:{chunk_id}"
        offered = {**source, "citation_id": citation}
        self.evidence[citation] = offered
        return offered

    async def search(self, query, top_k=None):
        await self.check()
        if self.searches >= 4:
            raise ValueError("Knowledge search limit reached for this turn (4 including initial search)")
        self.searches += 1
        self.retrieving = True
        try:
            result = await asyncio.to_thread(self.service.search, self.binding["kb_id"], query,
                                             top_k or self.binding["top_k"])
        finally:
            self.retrieving = False
        if self.revision is not None and result.revision != self.revision:
            raise ValueError("Knowledge corpus changed; please regenerate the answer/report")
        self.revision = result.revision
        hits = []
        for hit in result.hits:
            item = asdict(hit)
            item.update(text=hit.text[:READ_CHARS], truncated=len(hit.text) > READ_CHARS,
                        next_offset=READ_CHARS if len(hit.text) > READ_CHARS else None)
            hits.append(self._offer(item))
        return {"status": "hits" if hits else "no_hits", "revision": self.revision,
                "retrieval": result.retrieval, "hits": hits, "trust": "untrusted_document_data"}

    async def read(self, citation_id, offset=0):
        if citation_id not in self.evidence:
            raise ValueError("Unknown citation: only evidence provided in this turn may be read")
        await self.check()
        kb_id, chunk_id = citation_id.removeprefix("K:").split(":")
        sources = await asyncio.to_thread(self.service.source_context, kb_id, chunk_id)
        source = sources[0]
        if offset >= len(source["text"]) and offset != 0:
            raise ValueError("Read offset is past the end of the cited chunk")
        end = offset + READ_CHARS
        item = {**source, "text": source["text"][offset:end], "offset": offset,
                "truncated": end < len(source["text"]),
                "next_offset": end if end < len(source["text"]) else None}
        # Neighbours have their own citations; never relabel their text as the anchor.
        remaining = READ_CHARS - len(item["text"])
        neighbours = []
        for neighbour in sources[1:]:
            if remaining <= 0:
                break
            text = neighbour["text"][:remaining]
            neighbours.append(self._offer({**neighbour, "text": text,
                "truncated": len(text) < len(neighbour["text"]),
                "next_offset": len(text) if len(text) < len(neighbour["text"]) else None}))
            remaining -= len(text)
        return {"source": self._offer(item), "neighbours": neighbours,
                "revision": self.revision, "trust": "untrusted_document_data"}

    def cited(self, text):
        ids = list(dict.fromkeys("K:" + ref for ref in CITATIONS.findall(text)))
        unknown = [ref for ref in ids if ref not in self.evidence]
        if unknown:
            raise ValueError("Unverified citation(s), not provided this turn: " + ", ".join(unknown))
        return ids

    async def validate_answer(self, text, *, final=True):
        await self.check()
        self.cited(text)
        if final and self.report_error:
            raise ValueError("Report was not saved with verified citations: " + self.report_error)

    async def report(self, content):
        try:
            await self.check()
            ids = self.cited(content)
            if not ids:
                raise ValueError("A knowledge report requires at least one provided citation")
            sources = [{"citation_id": ref, "source_uri": self.evidence[ref]["source_uri"],
                        "generation_id": self.evidence[ref]["generation_id"],
                        "source_spans": self.evidence[ref]["source_spans"]} for ref in ids]
            return content + "\n\n## 来源记录\n\n```json\n" + json.dumps({
                "kb_id": self.binding["kb_id"], "revision": self.revision, "sources": sources,
                "citation_check": "存在且已于本轮提供；事实支持性需人工核对"},
                ensure_ascii=False, indent=2) + "\n```\n"
        except Exception as exc:
            self.report_error = str(exc)
            raise
