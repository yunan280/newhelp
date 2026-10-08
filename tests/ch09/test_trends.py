from datetime import UTC, datetime
from types import SimpleNamespace as NS


def row(id, *, score=0.8, count=32, hash="same"):
    return NS(id=id, created_at=datetime(2026, 10, 8, 0, 0, id, tzinfo=UTC), triggered_by="手动",
              dataset_size=40, metrics={"final_recall5": score, "final_recall5_N": count,
                                       "faithfulness": None, "faithfulness_N": 0,
                                       "_meta": {"comparison_hash": hash, "run_id": f"r{id}"}})


def test_na_and_config_drift_not_compared_as_drop(ch09_module):
    result = ch09_module("costs").eval_trend([
        row(4, hash="changed"), row(2, score=0.7), row(3, count=30), row(1)])
    assert [r["id"] for r in result] == ["1", "2", "3", "4"]
    assert abs(result[1]["changes"]["final_recall5"]["delta"] + 0.1) < 1e-8
    assert result[1]["changes"]["faithfulness"]["delta"] is None
    assert result[2]["changes"]["final_recall5"]["comparable"] is False
    assert result[3]["changes"]["final_recall5"]["comparable"] is False

