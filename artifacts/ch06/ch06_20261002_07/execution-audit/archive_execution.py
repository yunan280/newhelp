"""Archive only this plan's scratch evidence, verifying hashes before its cleanup."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from dotenv import dotenv_values

root = Path.cwd().resolve()
source = root / ".superpowers/sdd/2026-10-02-ch06-router"
target = root / "artifacts/ch06/ch06_20261002_07/execution-audit"
assert source.resolve() == root / ".superpowers/sdd/2026-10-02-ch06-router"
assert source.is_dir() and not target.exists()
files = sorted(path for path in source.rglob("*") if path.is_file())
assert files and not any(path.is_symlink() for path in source.rglob("*"))
secrets = {key: value.encode("utf-8") for key, value in dotenv_values(root / ".env").items()
           if value and len(value) >= 8 and any(word in key.upper()
                                              for word in ("API_KEY", "PASSWORD", "SECRET", "TOKEN"))}
for path in files:
    data = path.read_bytes()
    matches = [key for key, value in secrets.items() if value in data]
    if matches:
        raise ValueError(f"Sensitive values found in {path.relative_to(source)}: key names {matches}")
records = []
for path in files:
    relative = path.relative_to(source)
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    records.append({"path": relative.as_posix(), "bytes": destination.stat().st_size, "sha256": digest})
manifest = {"source": source.relative_to(root).as_posix(), "copy_verified": True,
            "fix_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "secret_value_scan": "no matches for configured secret values of at least 8 characters",
            "files": records}
(target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"archived_files": len(records), "sha256_verified": True,
                  "destination": target.relative_to(root).as_posix()}))
