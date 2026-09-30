"""Real PyMilvus request objects, replacing only the remote service."""

from dataclasses import replace

import pytest
from pymilvus import DataType, FunctionType, MilvusClient

from mewhelp.knowledge.store import ChunkSnapshot


class RemoteMilvus:
    create_schema = staticmethod(MilvusClient.create_schema)
    prepare_index_params = staticmethod(MilvusClient.prepare_index_params)

    def __init__(self):
        self.description = None
        self.indexes = {}
        self.created = None
        self.loaded = False
        self.rows = {}
        self.calls = []
        self.hits = [[{"id": 7, "entity": {"content_hash": "a" * 64}, "distance": 0.8}]]

    def has_collection(self, name):
        return self.description is not None

    def create_collection(self, **kwargs):
        self.created = kwargs
        self.description = kwargs["schema"].to_dict()
        self.indexes = {item.index_name: item.to_dict() for item in kwargs["index_params"]}

    def describe_collection(self, name):
        return self.description

    def list_indexes(self, name):
        return list(self.indexes)

    def describe_index(self, name, index_name):
        return self.indexes[index_name]

    def load_collection(self, name):
        self.loaded = True

    def upsert(self, **kwargs):
        for item in kwargs["data"]:
            self.rows[item["id"]] = item

    def delete(self, **kwargs):
        for key in kwargs["ids"]:
            self.rows.pop(key, None)

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return self.hits

    def hybrid_search(self, **kwargs):
        self.calls.append(("hybrid_search", kwargs))
        return self.hits

    def query_iterator(self, **kwargs):
        self.calls.append(("query_iterator", kwargs))
        values = list(self.rows.values())

        class Iterator:
            def next(self):
                result = values[:1]
                del values[:1]
                return result

            def close(self):
                pass

        return Iterator()


def index(client):
    from mewhelp.knowledge.vectors import HybridMilvusIndex

    return HybridMilvusIndex("http://localhost:19530", "knowledge_ch04", client=client)


def chunk():
    return ChunkSnapshot(7, "分类：参数\n问题：HX-210\n答案：蓝牙 5.3", "HX-210", "蓝牙 5.3", "手册 / 蓝牙", "参数", "耳机", "manual", False, "a" * 64)


def test_schema_uses_native_bm25_chinese_and_scalar_indexes():
    client = RemoteMilvus()
    index(client).ensure_collection()
    fields = {item["name"]: item for item in client.description["fields"]}
    assert fields["vector"]["params"]["dim"] == 1024
    assert fields["text"]["type"] == DataType.VARCHAR
    assert fields["text"]["params"]["analyzer_params"] == '{"type":"chinese"}'
    assert fields["product_category"]["nullable"] is True
    assert client.description["functions"][0]["type"] == FunctionType.BM25
    assert client.description["functions"][0]["input_field_names"] == ["text"]
    assert client.description["functions"][0]["output_field_names"] == ["sparse"]
    assert client.indexes["sparse"]["metric_type"] == "BM25"
    assert client.indexes["vector"]["metric_type"] == "COSINE"
    assert {"category", "product_category", "content_type", "is_key_clause"} <= client.indexes.keys()
    assert client.loaded


@pytest.mark.parametrize("bad_field", ["vector", "text", "product_category"])
def test_existing_incompatible_schema_is_not_silently_used(bad_field):
    client = RemoteMilvus()
    adapter = index(client)
    adapter.ensure_collection()
    field = next(item for item in client.description["fields"] if item["name"] == bad_field)
    if bad_field == "vector":
        field["params"]["dim"] = 768
    elif bad_field == "text":
        field["params"]["analyzer_params"] = '{"type":"standard"}'
    else:
        field["nullable"] = False
    with pytest.raises(RuntimeError, match="schema"):
        adapter.ensure_collection()


def test_existing_incompatible_index_is_rejected():
    client = RemoteMilvus()
    adapter = index(client)
    adapter.ensure_collection()
    client.indexes["sparse"]["metric_type"] = "IP"
    with pytest.raises(RuntimeError, match="index"):
        adapter.ensure_collection()


def test_hybrid_uses_two_filtered_top50_requests_and_rrf():
    from mewhelp.knowledge.filters import SearchFilters

    client = RemoteMilvus()
    hits = index(client).search("hybrid", vector=[0.1] * 1024, bm25_query="HX-210 蓝牙", filters=SearchFilters(product_category="耳机"))
    name, call = client.calls[-1]
    assert name == "hybrid_search"
    assert [req.limit for req in call["reqs"]] == [50, 50]
    assert [req.anns_field for req in call["reqs"]] == ["vector", "sparse"]
    assert call["ranker"].dict()["params"]["k"] == 60 and call["limit"] == 50
    assert call["output_fields"] == ["id", "content_hash"]
    assert all(req.expr == "product_category == {product_category}" for req in call["reqs"])
    assert all(req.expr_params == {"product_category": "耳机"} for req in call["reqs"])
    assert hits[0].id == 7 and hits[0].content_hash == "a" * 64


@pytest.mark.parametrize("strategy,field,data", [
    ("dense", "vector", [0.1] * 1024), ("bm25", "sparse", "HX-210 蓝牙"),
])
def test_single_leg_does_not_add_another_retrieval_strategy(strategy, field, data):
    from mewhelp.knowledge.filters import SearchFilters

    client = RemoteMilvus()
    index(client).search(strategy, vector=[0.1] * 1024 if strategy == "dense" else None, bm25_query="HX-210 蓝牙", filters=SearchFilters())
    name, call = client.calls[-1]
    assert name == "search" and call["anns_field"] == field and call["data"] == [data]
    assert call["limit"] == 50


def test_upsert_keeps_full_single_text_and_metadata():
    client = RemoteMilvus()
    index(client).upsert(chunk(), [0.1] * 1024)
    saved = client.rows[7]
    assert saved["text"] == "分类：参数\n问题：HX-210\n答案：蓝牙 5.3"
    assert saved["product_category"] == "耳机" and saved["content_hash"] == "a" * 64
    assert "sparse" not in saved  # Milvus produces it via its BM25 function.


@pytest.mark.parametrize("bad", [replace(chunk(), text="中" * 21846), replace(chunk(), category="中" * 341)])
def test_utf8_byte_overflow_is_not_truncated(bad):
    client = RemoteMilvus()
    with pytest.raises(ValueError, match="UTF-8"):
        index(client).upsert(bad, [0.1] * 1024)
    assert not client.rows


def test_invalid_dense_dimension_is_not_written():
    client = RemoteMilvus()
    with pytest.raises(ValueError, match="1024"):
        index(client).upsert(chunk(), [0.1] * 768)
    assert not client.rows


def test_audit_reports_missing_extra_hash_and_metadata_drift():
    client = RemoteMilvus()
    adapter = index(client)
    adapter.upsert(chunk(), [0.1] * 1024)
    assert adapter.audit([chunk()]) == []
    client.rows[7]["product_category"] = "充电器"
    client.rows[7]["content_hash"] = "b" * 64
    client.rows[9] = {**client.rows[7], "id": 9}
    report = adapter.audit([chunk(), replace(chunk(), id=8)])
    assert {(item["id"], item["kind"]) for item in report} == {
        ("7", "hash"), ("7", "metadata"), ("8", "missing"), ("9", "extra"),
    }
