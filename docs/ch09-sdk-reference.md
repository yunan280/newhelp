# Ch09 实测API与环境

2026-10-08先用Context7核对，再对安装包inspect；不是历史运行结果。

| 固定项 | 实际版本 |
| --- | --- |
| Python | 3.12.14 |
| FastAPI / SQLAlchemy | 0.141.1 / 2.1.1 |
| langchain-core / langchain-openai / LangGraph | 1.6.5 / 1.6.6 / 1.2.12 |
| PyMilvus | 2.6.17 |
| Langfuse SDK / Server | 4.17.0 / 4.54.0 |
| 追加的官方集成依赖 langchain | 1.4.3 |

原框架依赖使用旧环境freeze的完整约束，另建`.venv-ch09`安装，`pip check`确认兼容。原环境无pip，uv只读导出；langfuse.langchain.CallbackHandler会实际import langchain，单装SDK不会安装该可选框架包，补齐后原core/openai/graph版本未变化。

Context7 `/langfuse/langfuse-python`、`/langfuse/langfuse-docs`和`/websites/reference_langchain`返回官方签名；安装源码核对同名接口：

```python
Langfuse(*, public_key=None, secret_key=None, base_url=None,
         tracer_provider=None, span_exporter=None, timeout=None, **other_official_options)
CallbackHandler(*, public_key=None, trace_context=None)
Langfuse.start_as_current_observation(*, name, as_type='span', input=None,
                                     output=None, metadata=None, trace_context=None, ...)
propagate_attributes(*, user_id=None, session_id=None, metadata=None,
                     version=None, tags=None, trace_name=None, environment=None, ...)
Langfuse.flush() -> None
Langfuse.shutdown() -> None
Langfuse.auth_check() -> bool
client.api.projects.get(*, request_options=None) -> Projects  # .data
client.api.observations.get_many(*, fields=None, expand_metadata=None, limit=None,
    cursor=None, parse_io_as_json=None, name=None, user_id=None, session_id=None,
    type=None, trace_id=None, level=None, parent_observation_id=None,
    is_root_observation=None, environment=None, from_start_time=None,
    to_start_time=None, version=None, filter=None, request_options=None)
```

v2返回`response.data`和`response.meta.cursor`，不是next_cursor/page。fields支持core/basic/time/io/metadata/model/usage/prompt/metrics/trace_context；未指定只返回core/basic。**SDK4.17源码明确`parse_io_as_json=True`现已弃用、服务端返回400；IO为字符串，调用方自己解析JSON。** expand_metadata是需要展开的key逗号列表，不是布尔开关。

OTel metadata实际展开为`langfuse.observation.metadata.<key>`/`langfuse.trace.metadata.<key>`属性；不能把它当单个metadata JSON属性。业务意图写当前请求root observation，并在请求收尾用propagate_attributes更新根trace；不读共享handler.last_trace_id。后台根先attach空OTel Context，避免asyncio继承聊天根。图编译后with_config固定同一个handler；图内模型继承，旧图外模型显式复用同一回调。

官方镜像Next服务绑定容器HOSTNAME，容器内localhost/127.0.0.1健康探针实测ECONNREFUSED，但宿主API可读；healthcheck采用实际HOSTNAME:3000，不能据假探针故障换镜像。Compose只发布回环3039，全部6镜像已锁实际RepoDigest；关闭遥测，私有凭据不进Git/产物。

官方出处：[Python API](https://github.com/langfuse/langfuse-python/tree/main/_autodocs/api-reference)、[OTel属性](https://langfuse.com/integrations/native/opentelemetry)、[LangGraph回调](https://langfuse.com/integrations/frameworks/langgraph)、[4.54.0 Compose](https://github.com/langfuse/langfuse/blob/v4.54.0/docker-compose.yml)、[LangChain with_config](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable/with_config)。
