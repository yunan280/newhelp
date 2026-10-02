from contextlib import asynccontextmanager
from pathlib import Path
import sys
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mewhelp.main import app
from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.runtime import open_runtime
from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch06.policy_evaluation import isolated_policy_runtime
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import get_rag_runtime
from mewhelp.knowledge.api import get_kb_runtime, KbRuntime
from scripts.migrate_ch06_schema import migrate_ch06
workdir = Path('.cache/ch06/acceptance-01')
collection = 'ch06_eval_acceptance_20261002_01'
retrieval = isolated_policy_runtime(workdir, collection)
migrate_ch06(retrieval.session_factory.kw['bind'])
rag = get_rag_runtime(retrieval.session_factory, calibration_path=get_settings().rag_calibration_path, collection=collection)
@asynccontextmanager
async def lifespan(application):
    async with open_runtime(retrieval.session_factory,
        settings=Ch05Settings(checkpoint_path=workdir / 'workflow.sqlite3'),
        rag_factory=lambda: rag,
        router_settings=Ch06Settings(calibration_path=Path('artifacts/ch06/ch06_20261002_02/calibration-ui/router.json'),
          policy_calibration_path=Path('artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json'))) as runtime:
        application.state.ch05_runtime = runtime
        application.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(retrieval.session_factory, lambda _: None)
        yield
        application.dependency_overrides.clear()
        del application.state.ch05_runtime
app.router.lifespan_context = lifespan
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=9006, workers=1)
