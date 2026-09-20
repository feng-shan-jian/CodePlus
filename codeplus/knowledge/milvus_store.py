"""Profile-bound daily schemas and full-row writes; no alternative backend layer."""

import json
import math

from .retrieval import BM25_INDEX_PARAMS


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

    def ensure_collection(self, name: str, profile_hash: str, dimension: int, *, create=False, indexing=None):
        # An absent indexing contract is the original dense-only schema.
        schema = self._schema(f"codeplus:{profile_hash}", dimension,
                              indexing["analyzer"] if indexing is not None else None)
        if create and not self.client.has_collection(name):
            self.client.create_collection(name, schema=schema, consistency_level="Strong")
        self._validate_schema(self.client.describe_collection(name), schema.to_dict())
        expected = ({field: indexing[field] for field in ("dense", "sparse")} if indexing is not None else
                    {"dense": {"index_type": "FLAT", "metric_type": "COSINE", "params": {}}})
        existing = self._validate_indexes(name, expected, allow_missing=create)
        for field, spec in expected.items():
            if existing.get(field) != "Finished":
                index = self.client.prepare_index_params()
                index.add_index(field, **spec)
                self.client.create_index(name, index)
        # Both indexes must be complete before the service can commit READY.
        if create:
            self._validate_indexes(name, expected)
        self.client.load_collection(name)

    @staticmethod
    def _validate_schema(actual, expected):
        def fields(schema):
            result = []
            for field in schema["fields"]:
                params = dict(field.get("params", {}))
                # PyMilvus returns analyzer JSON and enable_analyzer as strings.
                for key in ("analyzer_params", "enable_analyzer"):
                    if isinstance(params.get(key), str):
                        params[key] = json.loads(params[key])
                result.append({
                    "name": field["name"], "type": field["type"], "params": params,
                    "default_value": field.get("default_value"),
                    "external_field": field.get("external_field", ""),
                    **{key: field.get(key, False) for key in
                       ("is_primary", "auto_id", "nullable", "is_partition_key", "is_clustering_key",
                        "is_dynamic", "is_function_output")},
                })
            return sorted(result, key=lambda field: field["name"])

        def functions(schema):
            return sorted([{
                "name": fn["name"], "type": fn["type"], "params": fn.get("params", {}),
                "input_field_names": list(fn["input_field_names"]),
                "output_field_names": list(fn["output_field_names"]),
            } for fn in schema.get("functions", [])], key=lambda fn: fn["name"])

        if (actual["description"] != expected["description"]
                or any(actual.get(key, False) != expected.get(key, False) for key in
                       ("auto_id", "enable_dynamic_field", "enable_namespace"))
                or actual.get("struct_array_fields") or actual.get("external_source")
                or fields(actual) != fields(expected) or functions(actual) != functions(expected)):
            raise ValueError("Milvus collection profile mismatch; fields/functions differ from the bound profile")

    def _validate_indexes(self, name, expected, *, allow_missing=False):
        found = {}
        for index_name in self.client.list_indexes(name):
            actual = self.client.describe_index(name, index_name)
            field = actual["field_name"]
            spec = expected.get(field)
            # SDK 3.0.2 describes build parameters as flattened strings; older
            # responses can carry a nested params object. Ignore only SDK metadata.
            params = dict(actual.get("params", {}))
            params.update({key: value for key, value in actual.items() if key not in
                           {"params", "field_name", "index_name", "index_type", "metric_type", "state",
                            "total_rows", "indexed_rows", "pending_index_rows"}})
            for key in ("bm25_k1", "bm25_b"):
                if key in params:
                    params[key] = float(params[key])
            if (spec is None or field in found
                    or any(actual.get(key) != spec[key] for key in ("index_type", "metric_type"))
                    or params != spec["params"]
                    or actual.get("state") not in (("Finished", "Unissued", "InProgress") if allow_missing else ("Finished",))):
                raise ValueError(f"Milvus collection profile mismatch; index {index_name} differs from the bound profile")
            found[field] = actual["state"]
        if not allow_missing and found.keys() != expected.keys():
            raise ValueError("Milvus collection profile mismatch; required index is missing")
        return found

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
                              params=BM25_INDEX_PARAMS.copy())
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
