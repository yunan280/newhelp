import importlib
from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def ch09_module():
    def require(name):
        try:
            return importlib.import_module('mewhelp.ch09.' + name)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith('mewhelp.ch09'):
                pytest.fail('Ch09 required contract is not implemented: ' + name)
            raise
    return require


@pytest.fixture
def span_client():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    public_key = 'pk-test-' + uuid4().hex
    client = Langfuse(public_key=public_key, secret_key='sk-test',
                      base_url='http://127.0.0.1:3039', tracer_provider=provider,
                      span_exporter=exporter)
    yield client, exporter, public_key
    client.shutdown()
