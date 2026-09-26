"""占位测试 —— 保证 pytest 能跑起来,后面每章补对应测试。"""

import mewhelp


def test_version_exists():
    assert mewhelp.__version__
