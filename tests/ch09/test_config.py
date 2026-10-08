import pytest


def test_enabled_cloud_url_is_rejected(ch09_module):
    settings = ch09_module('config').Ch09Settings
    with pytest.raises(ValueError):
        settings(enabled=True, base_url='https://cloud.langfuse.com',
                 public_key='pk-test', secret_key='sk-test')


@pytest.mark.parametrize('url', ['', 'http://user:password@localhost:3039',
                               'http://example.org', 'ftp://localhost:3039'])
def test_enabled_invalid_or_nonlocal_url_is_rejected(ch09_module, url):
    with pytest.raises(ValueError):
        ch09_module('config').Ch09Settings(enabled=True, base_url=url,
                                          public_key='pk-test', secret_key='sk-test')


def test_enabled_missing_credentials_is_rejected(ch09_module):
    with pytest.raises(ValueError):
        ch09_module('config').Ch09Settings(enabled=True, base_url='http://127.0.0.1:3039',
                                          public_key=None, secret_key=None)


def test_disabled_does_not_require_langfuse_configuration(ch09_module):
    assert not ch09_module('config').Ch09Settings(enabled=False).enabled
