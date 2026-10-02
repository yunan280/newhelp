from pathlib import Path
import subprocess

base = '74e325b'
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
subprocess.run(['git', 'merge-base', '--is-ancestor', base, head], check=True)
path = Path(__file__).with_name('review-package.diff')
with path.open('w', encoding='utf-8') as stream:
    stream.write(f'# Review package: {base}..{head}\nFrontend excluded by user; artifacts referenced separately, not expanded as huge diffs.\n')
    for arguments in [['log', '--oneline', f'{base}..{head}'], ['diff', '--stat', f'{base}..{head}'],
                      ['diff', '-U10', f'{base}..{head}', '--', '.', ':(exclude)artifacts/**',
                       ':(exclude)src/mewhelp/static/index.html']]:
        stream.write(subprocess.check_output(['git', *arguments]).decode('utf-8'))
        stream.write('\n')
print({'path': str(path), 'base': base, 'head': head, 'bytes': path.stat().st_size})
