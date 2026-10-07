"""运维可信本地插件；暂存成功后原子替换，不接受远程 Python。"""
import hashlib
from pathlib import Path
from threading import RLock
from types import ModuleType

from .registry import ToolRegistry


class PluginLoader:
    def __init__(self, directory: Path):
        self.directory = directory
        self._digests: dict[str, str] = {}
        self._lock = RLock()

    def refresh(self, registry: ToolRegistry) -> dict:
        report = {'loaded': [], 'removed': [], 'errors': {}}
        with self._lock:
            paths = sorted(self.directory.glob('*.py'))
            current = {p.stem for p in paths if not p.name.startswith('_')}
            for name in set(self._digests) - current:
                registry.replace_source(f'plugin:{name}', [])
                del self._digests[name]
                report['removed'].append(name)
            for path in paths:
                if path.stem not in current:
                    continue
                name = path.stem
                try:
                    source = path.read_bytes()
                    digest = hashlib.sha256(source).hexdigest()
                    if self._digests.get(name) == digest:
                        continue
                    staged = ToolRegistry()
                    module = ModuleType(f'mewhelp_plugin_{name}')
                    module.__file__ = str(path)
                    exec(compile(source, str(path), 'exec'), module.__dict__)
                    module.register(staged)
                    registry.replace_source(f'plugin:{name}', list(staged.snapshot().specs.values()))
                    self._digests[name] = digest
                    report['loaded'].append(name)
                except Exception as exc:
                    report['errors'][name] = f'{type(exc).__name__}: {exc}'
        return report
