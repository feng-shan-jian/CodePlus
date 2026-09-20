"""The fixed dense schema and full-row writes; no alternative backend layer."""

import math


class MilvusStore:
    def __init__(self, uri: str, *, timeout=30):
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri=uri, timeout=timeout)

    def check_health(self):
        return self.client.get_server_version(timeout=3)

    def close(self):
        self.client.close()

    def _schema(self, description: str, dimension: int, analyzer=None):
        from pymilvus import DataType

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False, description=description)
        for field in ("chunk_id", "doc_id", "generation_id", "text"):
            options = {"enable_analyzer": True, "analyzer_params": analyzer} if field == "text" and analyzer else {}
            schema.add_field(field, DataType.VARCHAR, is_primary=field == "chunk_id",
                             max_length=65535 if field == "text" else 64, **options)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dimension)
        if analyzer:
            from pymilvus import Function, FunctionType

            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(Function(name="text_bm25", input_field_names=["text"],
                                         output_field_names=["sparse"], function_type=FunctionType.BM25))
        return schema

    def ensure_collection(self, name: str, profile_hash: str, dimension: int, *, create=False):
        if create and not self.client.has_collection(name):
            schema = self._schema(f"codeplus:{profile_hash}", dimension)
            self.client.create_collection(name, schema=schema, consistency_level="Strong")
        description = self.client.describe_collection(name)
        dense = next(field for field in description["fields"] if field["name"] == "dense")
        if description["description"] != f"codeplus:{profile_hash}" or int(dense["params"]["dim"]) != dimension:
            raise ValueError("Milvus collection profile mismatch; create a separate knowledge base")
        if create and not self.client.list_indexes(name, field_name="dense"):
            index = self.client.prepare_index_params()
            index.add_index("dense", index_type="FLAT", metric_type="COSINE")
            self.client.create_index(name, index)
        self.client.load_collection(name)

    def create_experiment(self, name, profile_hash, dimension, rows, *, index_type, build_params, analyzer=None):
        """Fresh experiment only. Never mutate or bind an existing daily collection."""
        if not name.startswith("codeplus_eval_") or self.client.has_collection(name):
            raise ValueError("Experiment requires a new codeplus_eval_ collection")
        if index_type not in {"FLAT", "HNSW"}:
            raise ValueError("Experiment index must be FLAT or HNSW")
        schema = self._schema(f"codeplus-experiment:{profile_hash}", dimension, analyzer)
        self.client.create_collection(name, schema=schema, consistency_level="Strong", num_shards=1)
        # Flush the complete frozen batch before building, then synchronously load.
        self.client.insert(name, rows)
        self.client.flush(name)
        indexes = self.client.prepare_index_params()
        indexes.add_index("dense", index_type=index_type, metric_type="COSINE", params=build_params)
        if analyzer:
            indexes.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
                              params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
        self.client.create_index(name, indexes, timeout=180)
        self.client.load_collection(name, timeout=180)

    @staticmethod
    def rows(chunks, vectors, dimension: int) -> list[dict]:
        if len(chunks) != len(vectors):
            raise ValueError("Embedding count does not match chunks")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            if len(chunk.text.encode("utf-8")) > 65535:
                raise ValueError("Milvus text exceeds 65535 UTF-8 bytes; reduce chunk_tokens")
            if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
                raise ValueError("Embedding vector dimension or finite-value check failed")
            rows.append({"chunk_id": chunk.id, "doc_id": chunk.doc_id, "generation_id": chunk.generation_id,
                         "text": chunk.text, "dense": vector})
        return rows

    def upsert_chunks(self, name: str, rows: list[dict]):
        for start in range(0, len(rows), 128):
            self.client.upsert(name, rows[start:start + 128])
        self.client.flush(name)

    def verify_document(self, name: str, doc_id: str, expected: list[dict]):
        # Iterator avoids the normal query result window; compare all IDs and stored text.
        iterator = self.client.query_iterator(
            # PyMilvus 3.0.2's iterator forwards expr_params directly (unlike query/delete).
            name, filter="doc_id == {doc_id}", expr_params={"doc_id": doc_id},
            output_fields=["chunk_id", "doc_id", "generation_id", "text"], consistency_level="Strong",
        )
        found = []
        try:
            while batch := iterator.next():
                found.extend(batch)
        finally:
            iterator.close()
        wanted = [{key: value for key, value in row.items() if key != "dense"} for row in expected]
        if sorted(found, key=lambda row: row["chunk_id"]) != sorted(wanted, key=lambda row: row["chunk_id"]):
            raise ValueError("Milvus document verification failed: visible chunk set or text differs")

    @staticmethod
    def _scope(doc_ids):
        return {} if doc_ids is None else {"filter": "doc_id in {doc_ids}", "filter_params": {"doc_ids": doc_ids}}

    def search_dense(self, name: str, vector: list[float], top_k: int, *, doc_ids=None, ef=None):
        return self.client.search(name, [vector], anns_field="dense", limit=top_k,
                                  search_params={"metric_type": "COSINE", "params": {} if ef is None else {"ef": ef}},
                                  consistency_level="Strong", **self._scope(doc_ids))[0]

    def search_bm25(self, name: str, query: str, top_k: int, *, doc_ids=None):
        return self.client.search(name, [query], anns_field="sparse", limit=top_k,
                                  search_params={"metric_type": "BM25"}, consistency_level="Strong",
                                  **self._scope(doc_ids))[0]

    def delete_document(self, name: str, doc_id: str):
        """Idempotently erase just this document and verify the visible result."""
        self.client.delete(name, filter="doc_id == {doc_id}", filter_params={"doc_id": doc_id})
        self.client.flush(name)
        self.verify_document(name, doc_id, [])
