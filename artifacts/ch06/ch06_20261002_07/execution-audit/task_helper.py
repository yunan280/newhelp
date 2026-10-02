"""Windows equivalent of skill task-start/task-done; scratch only."""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
workspace = pathlib.Path(__file__).resolve().parent
plan = root / 'docs/superpowers/plans/2026-10-02-ch06-router.md'
action, number = sys.argv[1:3]
if action == 'start':
    lines = plan.read_text(encoding='utf-8').splitlines()
    selected, fenced, active = [], False, False
    for line in lines:
        if line.startswith('```'):
            fenced = not fenced
        match = re.match(r'^#+\s+Task\s+(\d+)\b', line) if not fenced else None
        if match:
            active = match[1] == number
        if active:
            selected.append(line)
    if not selected:
        raise SystemExit('missing task')
    brief = workspace / f'task-{number}-brief.md'
    brief.write_text('\n'.join(selected) + '\n', encoding='utf-8')
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    (workspace / f'task-{number}-base.txt').write_text(base, encoding='utf-8')
    print(brief)
    print('base:', base)
    print(brief.read_text(encoding='utf-8'))
elif action == 'done':
    command = sys.argv[3:]
    log = workspace / f'task-{number}-tests.log'
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    print('\n'.join(lines[-12:]))
    if result.returncode:
        raise SystemExit(result.returncode)
    base = (workspace / f'task-{number}-base.txt').read_text().strip()[:7]
    head = subprocess.check_output(['git', 'rev-parse', '--short=7', 'HEAD'], cwd=root, text=True).strip()
    summary = next(line for line in reversed(lines) if line.strip())
    with (workspace / 'progress.md').open('a', encoding='utf-8') as ledger:
        ledger.write(f'\nTask {number}: complete (commits {base}..{head}, tests: {command!r} → {summary})\n')
else:
    raise SystemExit('unknown action')
