"""The fixed dense schema and full-row writes; no alternative backend layer."""

import math


class MilvusStore:
    def __init__(self, uri: str):
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri=uri, timeout=30)

    def close(self):
        self.client.close()

    def ensure_collection(self, name: str, profile_hash: str, dimension: int, *, create=False):
        from pymilvus import DataType

        if create:
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False,
                                               description=f"codeplus:{profile_hash}")
            for field in ("chunk_id", "doc_id", "generation_id", "text"):
                schema.add_field(field, DataType.VARCHAR, is_primary=field == "chunk_id",
                                 max_length=65535 if field == "text" else 64)
            schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dimension)
            index = self.client.prepare_index_params()
            index.add_index("dense", index_type="FLAT", metric_type="COSINE")
            self.client.create_collection(name, schema=schema, index_params=index, consistency_level="Strong")
        description = self.client.describe_collection(name)
        dense = next(field for field in description["fields"] if field["name"] == "dense")
        if description["description"] != f"codeplus:{profile_hash}" or int(dense["params"]["dim"]) != dimension:
            raise ValueError("Milvus collection profile mismatch; create a separate knowledge base")
        self.client.load_collection(name)

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

    def search_dense(self, name: str, vector: list[float], top_k: int):
        return self.client.search(name, [vector], anns_field="dense", limit=top_k,
                                  search_params={"metric_type": "COSINE"}, consistency_level="Strong")[0]

    def delete_document(self, name: str, doc_id: str):
        """SDK operation for K11; user-facing removal/recovery belongs to S3."""
        self.client.delete(name, filter="doc_id == {doc_id}", filter_params={"doc_id": doc_id})
        self.client.flush(name)
        self.verify_document(name, doc_id, [])
