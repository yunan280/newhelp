# Ch04 四策略真实对比

Run: `ch04_20260930_01`；示例业务隔离集合 `ch04_eval_ch04_20260930_01`。

只比较 test 集；四策略共用冻结原文、查询缓存、可信过滤和生成 Prompt。消融均不启用生产相关性阈值，不向在线问题池写入。拒答的 Faithfulness 为 NA，错误单独统计。

|策略/桶|N|Recall@50|MRR@50|Recall@5|Recall@10|MRR@10|Faithfulness|回答覆盖|有效评分覆盖|正确拒答/误拒/误放|错误/评分错误|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
|dense/all|40|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.5000|0.5000|2/0/0|18/0|
|dense/colloquial|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/model_exact|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/multi_constraint|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|8/0|
|dense/synonym|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.5000|0.5000|0/0/0|4/0|
|dense/unanswerable|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|6/0|
|dense/easy|15|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.6000|0.6000|2/0/0|4/0|
|dense/hard|10|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.4000|0.4000|0/0/0|6/0|
|dense/medium|15|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.4667|0.4667|0/0/0|8/0|
|dense/colloquial/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/colloquial/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/colloquial/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/model_exact/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/model_exact/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/model_exact/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/multi_constraint/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|dense/multi_constraint/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|dense/multi_constraint/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|dense/synonym/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|dense/synonym/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|dense/synonym/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.3333|0.3333|0/0/0|2/0|
|dense/unanswerable/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|1/0|
|dense/unanswerable/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|dense/unanswerable/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|bm25/all|40|1.0000|0.9417|1.0000|1.0000|0.9417|0.9833|0.5000|0.5000|2/0/0|18/0|
|bm25/colloquial|8|1.0000|0.8542|1.0000|1.0000|0.8542|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/model_exact|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/multi_constraint|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|8/0|
|bm25/synonym|8|1.0000|1.0000|1.0000|1.0000|1.0000|0.9167|0.5000|0.5000|0/0/0|4/0|
|bm25/unanswerable|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|6/0|
|bm25/easy|15|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.6000|0.6000|2/0/0|4/0|
|bm25/hard|10|1.0000|0.8333|1.0000|1.0000|0.8333|1.0000|0.4000|0.4000|0/0/0|6/0|
|bm25/medium|15|1.0000|0.9286|1.0000|1.0000|0.9286|0.9524|0.4667|0.4667|0/0/0|8/0|
|bm25/colloquial/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/colloquial/hard|2|1.0000|0.6667|1.0000|1.0000|0.6667|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/colloquial/medium|3|1.0000|0.8333|1.0000|1.0000|0.8333|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/model_exact/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/model_exact/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/model_exact/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/multi_constraint/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|bm25/multi_constraint/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|bm25/multi_constraint/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|bm25/synonym/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|bm25/synonym/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|bm25/synonym/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|0.6667|0.3333|0.3333|0/0/0|2/0|
|bm25/unanswerable/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|1/0|
|bm25/unanswerable/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|bm25/unanswerable/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid/all|40|1.0000|0.9750|1.0000|1.0000|0.9750|1.0000|0.5000|0.5000|2/0/0|18/0|
|hybrid/colloquial|8|1.0000|0.9375|1.0000|1.0000|0.9375|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/model_exact|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/multi_constraint|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|8/0|
|hybrid/synonym|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.5000|0.5000|0/0/0|4/0|
|hybrid/unanswerable|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|6/0|
|hybrid/easy|15|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.6000|0.6000|2/0/0|4/0|
|hybrid/hard|10|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.4000|0.4000|0/0/0|6/0|
|hybrid/medium|15|1.0000|0.9286|1.0000|1.0000|0.9286|1.0000|0.4667|0.4667|0/0/0|8/0|
|hybrid/colloquial/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/colloquial/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/colloquial/medium|3|1.0000|0.8333|1.0000|1.0000|0.8333|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/model_exact/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/model_exact/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/model_exact/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/multi_constraint/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid/multi_constraint/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid/multi_constraint/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid/synonym/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid/synonym/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid/synonym/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.3333|0.3333|0/0/0|2/0|
|hybrid/unanswerable/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|1/0|
|hybrid/unanswerable/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid/unanswerable/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid_rerank/all|40|1.0000|0.9737|1.0000|1.0000|1.0000|1.0000|0.4750|0.4750|2/0/0|19/0|
|hybrid_rerank/colloquial|8|1.0000|0.9375|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/model_exact|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/multi_constraint|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|8/0|
|hybrid_rerank/synonym|8|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.3750|0.3750|0/0/0|5/0|
|hybrid_rerank/unanswerable|8|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|6/0|
|hybrid_rerank/easy|15|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.6000|0.6000|2/0/0|4/0|
|hybrid_rerank/hard|10|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0.4000|0.4000|0/0/0|6/0|
|hybrid_rerank/medium|15|1.0000|0.9167|1.0000|1.0000|1.0000|1.0000|0.4000|0.4000|0/0/0|9/0|
|hybrid_rerank/colloquial/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/colloquial/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/colloquial/medium|3|1.0000|0.8333|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/model_exact/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/model_exact/hard|2|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/model_exact/medium|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/multi_constraint/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid_rerank/multi_constraint/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid_rerank/multi_constraint/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid_rerank/synonym/easy|3|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|1.0000|0/0/0|0/0|
|hybrid_rerank/synonym/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid_rerank/synonym/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|
|hybrid_rerank/unanswerable/easy|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|2/0/0|1/0|
|hybrid_rerank/unanswerable/hard|2|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|2/0|
|hybrid_rerank/unanswerable/medium|3|NA|NA|NA|NA|NA|NA|0.0000|0.0000|0/0/0|3/0|

运行状态：completed_with_errors；耗时 371.58s。

每项指标的有效分母见 summary.json 的 *_N；实际问题、原文、声明与理由、耗时和失败见 cases.jsonl。LLM judge 可能误判，不能把此分数当人工审核结论。

## 校准与实际配置

```json
{
  "calibration": {
    "threshold": 0.9556965231895447,
    "model_metadata": {
      "model_id": "BAAI/bge-reranker-v2-m3",
      "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
      "max_length": 8192,
      "score_transform": "sigmoid"
    },
    "corpus_hash": "4348decce0c4b8f81edebb0d15df8afc5213aa8ca85ee0f555c5fbc46cca55a4",
    "query_hash": "aed316a9d3560560d2828abfc72b1e2468d96c721c7b8e38f20f66fe2d93adc8",
    "false_allow": 0,
    "false_refuse": 4
  },
  "configuration": {
    "candidate_top_k_each": 50,
    "fused_top_k": 50,
    "final_top_k": 10,
    "rrf_k": 60,
    "analyzer": "chinese",
    "bm25": "Milvus native Function",
    "production_relevance_gate": false,
    "pool_writes": false,
    "context_budget": 32000,
    "generation_model": "deepseek-chat",
    "temperature": 0,
    "method": "function_calling",
    "judge_model": "deepseek-chat",
    "prompt_hashes": {
      "query": "ce5d74ae62fa6e63bf7c698b8f17b505846b11bbfd5e21118b8c8234dd1875f2",
      "answer": "82910838b0c317e0e7a2ecb3661523428e3603014b551892e7e4a764c05173d2",
      "judge": "a0029a5089b5360b58b4e9365040258376cee7447dd8740048da559b3065c99e"
    },
    "dependencies": {
      "pymilvus": "2.6.17",
      "sentence-transformers": "3.4.1",
      "transformers": "4.57.6",
      "torch": "2.14.0",
      "SQLAlchemy": "2.1.1",
      "langchain-core": "1.6.5",
      "langchain-openai": "1.6.6",
      "pydantic": "2.13.5"
    }
  },
  "frozen_run": {
    "run_id": "ch04_20260930_01",
    "collection": "ch04_eval_ch04_20260930_01",
    "frozen": true,
    "ready": true,
    "input_corpus_hash": "38e1da505d18474c9b9f35a7b8f12c96526c3a981dee2c4508f2270379bf5ae2",
    "input_query_hash": "283bed271404be2cbd343bac276bfc03d4749682c050844ad03efb537de5d085",
    "corpus_hash": "4348decce0c4b8f81edebb0d15df8afc5213aa8ca85ee0f555c5fbc46cca55a4",
    "query_hash": "aed316a9d3560560d2828abfc72b1e2468d96c721c7b8e38f20f66fe2d93adc8",
    "query_count": 60,
    "corpus_count": 80,
    "prompt_hashes": {
      "query": "ce5d74ae62fa6e63bf7c698b8f17b505846b11bbfd5e21118b8c8234dd1875f2",
      "answer": "82910838b0c317e0e7a2ecb3661523428e3603014b551892e7e4a764c05173d2",
      "judge": "a0029a5089b5360b58b4e9365040258376cee7447dd8740048da559b3065c99e"
    },
    "index_audit_errors": 0,
    "query_cache_hash": "e6df6cd705aa85e0221c25251f9792241bf084a73bc22e55e9febd331ac5559a",
    "model_metadata": {
      "embedding": {
        "model_id": "BAAI/bge-m3",
        "revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "dimension": 1024,
        "normalize_embeddings": true
      },
      "reranker": {
        "model_id": "BAAI/bge-reranker-v2-m3",
        "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "max_length": 8192,
        "score_transform": "sigmoid"
      }
    },
    "comparison_status": "completed_with_errors"
  }
}
```

## 失败与误拒/误放样例

- synonym-06 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 137184d0-2ed3-4c79-9b54-32f13905b307)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-07 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: f578a17f-11a1-40a5-8270-b2fa2cf99922)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-07 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8773d0a2-3275-4cd4-88bf-77100460bf01)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-07 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: dc83080e-7675-4fc3-a40e-c7ba3d51838f)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-07 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a89348fb-8fa1-4ba5-8859-d99a7f6d796a)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-08 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 38de4dfa-a96b-434a-949c-33d61fb96d91)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-08 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: d83a6174-d53a-4909-bcd5-99fe7dfbdf81)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-08 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 36f7e94a-d200-4c21-8c5c-14ddcf079609)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-08 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a5f58de2-a2bf-48f2-bc15-38a99a20113c)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-11 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 3db92962-a04a-4d08-92d5-ab0cf52d0502)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-11 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 28d20374-efbc-4c69-9248-92d426aaf716)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-11 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 0f04bd19-5642-4825-a173-3db54a586e6c)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-11 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: c5e43694-214f-49bf-8453-36e43ccd0a34)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-12 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 76189427-bc36-4064-b98a-f83ff2e61656)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-12 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: c79bb95d-7b7c-4bbb-8f4a-b5fea8c69968)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-12 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 28c9d610-0972-4565-b2f8-cc46ec88e642)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- synonym-12 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8d7f59a9-e0c2-4d2c-975b-89ea7e245712)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-02 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 4e542cd5-e20b-4070-b30a-9413a78e7cae)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-02 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: e1cda6d8-718e-4823-beb3-5ae257964519)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-02 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 723f81d3-e92a-4b64-87ec-8d3dab683f8d)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-02 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 017321a9-6930-4b2d-9c65-04da55df30d8)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-03 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 055d7d70-99b4-42d7-b262-47fbfcd91ca1)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-03 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: cd908581-84f2-4ff4-825e-d8ad3874efd5)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-03 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 6858c711-4cf6-46d8-9cfa-cb1ba5ac238b)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-03 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 0936d828-371a-474a-8958-33981d767887)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-04 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8ce8aa1c-f931-47a4-b847-6a3bcf004b8a)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-04 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 68bae17d-fdb3-4976-a6e8-0e1b5e662584)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-04 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: d475298f-ac48-4cc1-9993-304955e1da96)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-04 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 64a403b7-7d76-4376-834e-ac5597886f61)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-06 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 0156cf70-09c0-4762-8bd1-b79dd9c17906)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-06 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 3aae9b99-c85b-43d8-8070-167cee9b5d5a)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-06 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a9dc3458-006e-4e14-83be-b69eae400d1e)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-06 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a8087527-941c-4e54-9811-72e53b262106)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-07 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: cc77de93-e033-43c3-9764-77cf669a4d01)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-07 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 7ed5d733-96e8-412e-8370-7e345c651f47)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-07 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 605daa2a-8ff2-4df4-b597-29391a6088fa)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-07 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 7f20f020-00ac-406f-84da-a9bf89a7b659)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-08 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: dfb510e8-d0fc-414b-9040-1e03fd2ddc27)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-08 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a3b17c88-629e-4087-a6d9-90edff38358a)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-08 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 5d412f6e-ee38-4514-ba3e-e0090257859b)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-08 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 1156ad44-bebc-47df-8f4b-7a3b02a4ad9f)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-11 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 4b880863-6476-41f1-9f0b-5a2a566b65f0)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-11 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: ba70e03f-3925-47ba-9cf1-bbbdbe8345a5)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-11 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 4e90b8f7-a902-4648-a2de-98c8f9aacac1)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-11 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: e545e1d5-c1f3-4938-ba12-1155b6da38f6)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-12 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a147565d-3a27-4b5f-bc01-f0e0c626f8ba)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-12 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: b0b7dd87-3641-48da-ae95-646e5db65992)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-12 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8d23c79f-37f0-4a76-b8c7-fea1ac44e3e2)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- multi_constraint-12 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: e271198e-1adc-4be9-8e08-34a0d5857fc0)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-02 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 45621ae2-c893-4bc9-8d55-4cd545e00cb2)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-02 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 44887acd-4f18-4c4c-a806-184a34fd732b)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-02 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 732e62cc-4aed-46d5-a6d7-71d968dc5fee)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-03 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 98253374-1cee-45ca-a252-c02e2720da6e)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-06 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 9cfe09c5-e49e-47b9-9b4a-3f98c52dabac)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-06 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: bc315f68-f132-4720-b58a-b93a45795618)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-06 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: ceb2d1fc-fff0-4ace-b2bf-3591397a5f64)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-06 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: e7de40d7-8876-4527-beab-f9e36a3e4b73)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-07 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8101da91-e4a3-493a-b471-4af9e8e384e5)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-07 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 81236cab-14cc-447f-877b-781fe637194e)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-07 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 46582e9e-e176-4b78-a23b-6de4e3b9880b)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-07 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 45e9866e-6cd1-4b2b-b575-ac5715af20ee)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-08 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 42b94394-b18d-4944-861a-0230e57b51a1)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-08 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: d1b79bd5-7e95-435f-b679-f9c2122fc781)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-08 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 282becae-ef48-4d4f-8acb-15582a2d2ca4)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-08 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: c1c5e425-f12c-42c8-8462-4dfb0c0c9f39)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-11 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: a8661bee-5ae3-47c1-b12a-76989380a9e0)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-11 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 9d30404b-3d59-4d27-b5ed-f0fe945a5906)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-11 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 336f7415-d2ce-45cf-a717-1fc5f2a03ef5)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-11 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 94301f04-bedf-445b-b4c2-a209b340c9d9)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-12 / dense：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 350bbba5-42fa-487c-91e2-adfcb5284ca9)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-12 / bm25：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: fc0f08d2-99d0-4502-a3b0-0c7b8c4f3c92)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-12 / hybrid：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 8adddf5c-f1c8-47b4-bd70-14a4a6b00089)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
- unanswerable-12 / hybrid_rerank：APIStatusError: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 7759662b-b266-43ae-a523-9f7caf713176)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}；评分错误=None。
