"""Create private local credentials once; never print them."""
import secrets
from pathlib import Path


def initialize(root: Path):
    target = root / '.env.ch09.langfuse'
    if target.exists():
        print('Local Langfuse credentials already exist; preserving them.')
        return target
    values = {key: secrets.token_hex(32) for key in (
        'POSTGRES_PASSWORD', 'CLICKHOUSE_PASSWORD', 'REDIS_AUTH', 'MINIO_ROOT_PASSWORD',
        'SALT', 'ENCRYPTION_KEY', 'NEXTAUTH_SECRET', 'LANGFUSE_INIT_USER_PASSWORD')}
    values.update(LANGFUSE_PUBLIC_KEY='pk-lf-' + secrets.token_hex(16),
                  LANGFUSE_SECRET_KEY='sk-lf-' + secrets.token_hex(32),
                  LANGFUSE_BASE_URL='http://127.0.0.1:3039',
                  LANGFUSE_INIT_USER_EMAIL='reviewer@mewhelp.local', CH09_ENABLED='true')
    values['DATABASE_URL'] = (
        'postgresql://postgres:' + values['POSTGRES_PASSWORD'] + '@postgres:5432/postgres')
    with target.open('x', encoding='utf-8') as output:
        output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
    print('Created ignored .env.ch09.langfuse; credentials were not printed.')
    return target


if __name__ == '__main__':
    initialize(Path(__file__).resolve().parent.parent)
