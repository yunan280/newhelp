"""Read real dependencies before launching; never call an upstream model here."""
import json
from types import SimpleNamespace

from langfuse import Langfuse
from sqlalchemy import text

from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch09.confidence import load_workflow_confidence
from mewhelp.ch09.config import Ch09Settings
from mewhelp.ch09.migration import migrate_ch09
from mewhelp.db.engine import engine
from mewhelp.knowledge.answering import load_relevance_threshold
from mewhelp.knowledge.reranking import reranker_metadata
from mewhelp.knowledge.vectors import MilvusSettings


def main():
    from mewhelp.ch05.config import Ch05Settings
    from mewhelp.ch05.intent import load_router_calibration
    from mewhelp.ch07.budget import compute_budget, measured_prefix
    from mewhelp.ch07.config import ContextSettings, load_profile
    from mewhelp.config import get_settings

    settings = Ch09Settings()
    confidence = load_workflow_confidence(settings)
    context = ContextSettings()
    if confidence.top_k != context.rerank_top_k:
        raise ValueError("TopK differs from the formal profile")
    budget_profile = load_profile(context.context_calibration_path)
    compute_budget(context, budget_profile, actual_fixed={"prefix": measured_prefix(budget_profile)})
    router = Ch06Settings()
    load_router_calibration(SimpleNamespace(router_settings=router, limits=Ch05Settings().limits()))
    rag = get_settings()
    if not rag.rag_context_budget or not rag.rag_calibration_path:
        raise ValueError("RAG budget/calibration must be explicit")
    load_relevance_threshold(rag.rag_calibration_path, reranker_metadata())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    schema = migrate_ch09(engine)
    index = MilvusSettings().connect_hybrid()
    client = None
    try:
        if not index.client.has_collection(index.collection):
            raise RuntimeError("Configured online Milvus collection does not exist")
        index.ensure_collection()
        count = len(index.client.query(index.collection, filter="id > 0", output_fields=["id"],
                                       limit=1, consistency_level="Strong", timeout=10))
        if not count:
            raise RuntimeError("Online Milvus collection has no published knowledge")
        client = Langfuse(public_key=settings.public_key, secret_key=settings.secret_key,
                          base_url=settings.base_url, timeout=5)
        if not client.auth_check():
            raise RuntimeError("Local Langfuse project authentication failed")
        projects = client.api.projects.get()
        print(json.dumps({"mysql_select_1": True, "schema_changes": schema["changed"],
                          "milvus_collection": index.collection, "published_knowledge": True,
                          "langfuse_projects": [p.name for p in projects.data],
                          "confidence_profile": str(settings.confidence_path),
                          "checkpoint": "independent Ch09 path"}, ensure_ascii=False))
    finally:
        index.client.close()
        if client:
            client.shutdown()
        engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=9030)
    args = parser.parse_args()
    main()
    if args.serve:
        import uvicorn

        uvicorn.run("mewhelp.main:app", host="127.0.0.1", port=args.port, workers=1)
