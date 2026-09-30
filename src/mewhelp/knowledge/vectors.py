"""PyMilvus 2.6 native hybrid index; MySQL remains the original-text authority."""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from .filters import SearchFilters, compile_filter
from .store import ChunkSnapshot

Strategy = Literal["dense", "bm25", "hybrid", "hybrid_rerank"]
VARCHAR_BYTES = {"text": 65535, "category": 1020, "product_category": 512, "content_type": 128, "content_hash": 64}
METADATA_FIELDS = ("category", "product_category", "content_type", "is_key_clause")


@dataclass(frozen=True)
class SearchHit:
    id: int
    content_hash: str


class MilvusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "knowledge"

    def connect(self) -> "MilvusVectors":
        return MilvusVectors(f"http://{self.milvus_host}:{self.milvus_port}", self.milvus_collection)

    def connect_hybrid(self, *, collection: str | None = None) -> "HybridMilvusIndex":
        return HybridMilvusIndex(
            f"http://{self.milvus_host}:{self.milvus_port}", collection or self.milvus_collection,
        )


class MilvusVectors:
    def __init__(self, uri: str, collection: str):
        self.client = MilvusClient(uri=uri)
        self.collection = collection

    def ensure_collection(self) -> None:
        if not self.client.has_collection(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                dimension=1024,
                primary_field_name="id",
                id_type="int",
                vector_field_name="vector",
                metric_type="COSINE",
                auto_id=False,
            )

    def upsert(self, row_id: int | ChunkSnapshot, vector: list[float]) -> None:
        # Explicit legacy dense adapter, used only by the old application path.
        row_id = row_id.id if isinstance(row_id, ChunkSnapshot) else row_id
        self.client.upsert(
            collection_name=self.collection,
            data={"id": row_id, "vector": vector},
        )

    def delete(self, ids: list[int]) -> None:
        if ids:
            self.client.delete(collection_name=self.collection, ids=ids)

    def search(self, vector: list[float], limit: int = 3) -> list[int]:
        hits = self.client.search(
            collection_name=self.collection,
            data=[vector],
            limit=limit,
            search_params={"metric_type": "COSINE", "params": {}},
        )
        return [int(hit["id"]) for hit in hits[0]]


class HybridMilvusIndex:
    def __init__(self, uri: str, collection: str, *, client=None):
        self.client = client if client is not None else MilvusClient(uri=uri)
        self.collection = collection

    def ensure_collection(self) -> None:
        if not self.client.has_collection(self.collection):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
            schema.add_field(
                "text", DataType.VARCHAR, max_length=VARCHAR_BYTES["text"],
                enable_analyzer=True, analyzer_params={"type": "chinese"},
            )
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            for name in ("category", "product_category", "content_type", "content_hash"):
                schema.add_field(
                    name, DataType.VARCHAR, max_length=VARCHAR_BYTES[name],
                    nullable=name in ("product_category", "content_type"),
                )
            schema.add_field("is_key_clause", DataType.BOOL)
            schema.add_function(Function(
                name="text_bm25", function_type=FunctionType.BM25,
                input_field_names=["text"], output_field_names=["sparse"],
            ))
            indexes = self.client.prepare_index_params()
            indexes.add_index(field_name="vector", index_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            indexes.add_index(field_name="sparse", index_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
            for name in METADATA_FIELDS:
                indexes.add_index(field_name=name, index_name=name, index_type="INVERTED")
            self.client.create_collection(
                collection_name=self.collection, schema=schema, index_params=indexes,
                consistency_level="Strong",
            )
        self._validate_schema(self.client.describe_collection(self.collection))
        names = set(self.client.list_indexes(self.collection))
        expected = {"vector": ("AUTOINDEX", "COSINE"), "sparse": ("SPARSE_INVERTED_INDEX", "BM25")}
        expected.update({name: ("INVERTED", None) for name in METADATA_FIELDS})
        for name, (kind, metric) in expected.items():
            if name not in names:
                raise RuntimeError(f"Incompatible Milvus index: missing {name}")
            details = self.client.describe_index(self.collection, name)
            if details.get("index_type") != kind or (metric and details.get("metric_type") != metric):
                raise RuntimeError(f"Incompatible Milvus index: {name}")
        # PyMilvus load_collection waits for loading by default (_async=False).
        self.client.load_collection(self.collection)

    @staticmethod
    def _validate_schema(description: dict) -> None:
        fields = {field["name"]: field for field in description.get("fields", [])}
        expected_types = {
            "id": DataType.INT64, "vector": DataType.FLOAT_VECTOR, "sparse": DataType.SPARSE_FLOAT_VECTOR,
            "is_key_clause": DataType.BOOL, **{name: DataType.VARCHAR for name in VARCHAR_BYTES},
        }
        def invalid():
            raise RuntimeError("Incompatible Milvus schema; rebuild a separate collection")

        if set(fields) != set(expected_types) or description.get("auto_id", False) or description.get("enable_dynamic_field", False):
            invalid()
        for name, kind in expected_types.items():
            field = fields[name]
            if field.get("type") != kind:
                invalid()
            if bool(field.get("nullable", False)) != (name in ("product_category", "content_type")):
                invalid()
            if name in VARCHAR_BYTES and int(field.get("params", {}).get("max_length", 0)) != VARCHAR_BYTES[name]:
                invalid()
        if not fields["id"].get("is_primary") or int(fields["vector"].get("params", {}).get("dim", 0)) != 1024:
            invalid()
        params = fields["text"].get("params", {})
        analyzer = params.get("analyzer_params", {})
        if isinstance(analyzer, str):
            try:
                analyzer = json.loads(analyzer)
            except ValueError:
                invalid()
        enabled = params.get("enable_analyzer") in (True, "true", "True")
        if not enabled or analyzer != {"type": "chinese"}:
            invalid()
        functions = description.get("functions", [])
        if not any(
            fn.get("type") == FunctionType.BM25 and fn.get("input_field_names") == ["text"]
            and fn.get("output_field_names") == ["sparse"] for fn in functions
        ):
            invalid()

    def upsert(self, snapshot: ChunkSnapshot, vector: list[float]) -> None:
        if len(vector) != 1024:
            raise ValueError("BGE-M3 vector must have 1024 dimensions")
        row = {
            "id": snapshot.id, "vector": vector, "text": snapshot.text,
            "content_hash": snapshot.content_hash,
            **{name: getattr(snapshot, name) for name in METADATA_FIELDS},
        }
        for name, limit in VARCHAR_BYTES.items():
            value = row[name]
            if value is not None and len(value.encode("utf-8")) > limit:
                raise ValueError(f"{name} exceeds Milvus UTF-8 byte limit {limit}; original retained")
        self.client.upsert(collection_name=self.collection, data=[row])

    def delete(self, ids: list[int]) -> None:
        if ids:
            self.client.delete(collection_name=self.collection, ids=ids)

    def search(self, strategy: Strategy, *, vector: list[float] | None, bm25_query: str, filters: SearchFilters) -> list[SearchHit]:
        expr, params = compile_filter(filters)
        if strategy not in ("dense", "bm25", "hybrid", "hybrid_rerank"):
            raise ValueError(f"Unknown strategy: {strategy}")
        if strategy != "bm25" and (vector is None or len(vector) != 1024):
            raise ValueError("Dense search requires a 1024-dimensional vector")
        outputs = ["id", "content_hash"]
        if strategy in ("hybrid", "hybrid_rerank"):
            requests = [
                AnnSearchRequest(data=[vector], anns_field="vector", param={"metric_type": "COSINE"}, limit=50, expr=expr, expr_params=params),
                AnnSearchRequest(data=[bm25_query], anns_field="sparse", param={"metric_type": "BM25"}, limit=50, expr=expr, expr_params=params),
            ]
            hits = self.client.hybrid_search(
                collection_name=self.collection, reqs=requests, ranker=RRFRanker(k=60),
                limit=50, output_fields=outputs, consistency_level="Strong",
            )
        else:
            dense = strategy == "dense"
            hits = self.client.search(
                collection_name=self.collection, data=[vector if dense else bm25_query],
                anns_field="vector" if dense else "sparse", filter=expr, filter_params=params,
                search_params={"metric_type": "COSINE" if dense else "BM25"},
                limit=50, output_fields=outputs, consistency_level="Strong",
            )
        return [SearchHit(int(hit["id"]), hit["entity"]["content_hash"]) for hit in hits[0]]

    def audit(self, snapshots: list[ChunkSnapshot]) -> list[dict]:
        expected = {item.id: item for item in snapshots}
        actual = {}
        iterator = self.client.query_iterator(
            collection_name=self.collection, batch_size=1000, filter="",
            output_fields=["id", "content_hash", *METADATA_FIELDS], consistency_level="Strong",
        )
        try:
            while batch := iterator.next():
                actual.update({int(item["id"]): item for item in batch})
        finally:
            iterator.close()
        report = []
        for row_id in sorted(expected.keys() | actual.keys()):
            if row_id not in actual:
                report.append({"id": str(row_id), "kind": "missing"})
            elif row_id not in expected:
                report.append({"id": str(row_id), "kind": "extra"})
            else:
                snapshot, row = expected[row_id], actual[row_id]
                if snapshot.content_hash != row.get("content_hash"):
                    report.append({"id": str(row_id), "kind": "hash"})
                if any(getattr(snapshot, name) != row.get(name) for name in METADATA_FIELDS):
                    report.append({"id": str(row_id), "kind": "metadata"})
        return report
