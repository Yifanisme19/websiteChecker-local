"""Database-backed, non-expiring internal API tokens. No public management API."""
import argparse
import getpass
import hashlib
import json
import secrets
from time import time

import pymysql

from app.database import connect


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def create_token(settings, name, note='', *, legacy=None):
    name = name.strip()
    if not name or len(name) > 100 or len(note) > 1000:
        raise ValueError('Name must be 1-100 characters; note at most 1000 characters')
    value = legacy if legacy is not None else f'wc_{settings.app_env}_' + secrets.token_urlsafe(32)
    if not value or len(value) > 4096 or any(c.isspace() for c in value):
        raise ValueError('Token must be non-empty and contain no whitespace (maximum 4096 characters)')
    digest = token_hash(value)
    # A fingerprint identifies the record without exposing any of the secret.
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO api_tokens (name, note, token_hash, fingerprint, created_at, created_by) '
                           'VALUES (%s,%s,%s,%s,%s,%s)',
                           (name, note, digest, digest[:12], int(time()), getpass.getuser()))
            token_id = cursor.lastrowid
        connection.commit()
    return token_id, value


def authenticate_token(settings, value):
    if not value or len(value) > 4096:
        return None
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM api_tokens WHERE token_hash=%s AND revoked_at IS NULL FOR UPDATE',
                           (token_hash(value),))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute('UPDATE api_tokens SET last_used_at=%s WHERE id=%s', (int(time()), row[0]))
        connection.commit()
    return row[0]


def list_tokens(settings):
    with connect(settings) as connection:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute('SELECT id, name, note, fingerprint, created_at, created_by, last_used_at, '
                           'revoked_at, revoked_by FROM api_tokens ORDER BY id DESC')
            return cursor.fetchall()


def revoke_token(settings, token_id):
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute('UPDATE api_tokens SET revoked_at=%s, revoked_by=%s '
                           'WHERE id=%s AND revoked_at IS NULL', (int(time()), getpass.getuser(), token_id))
            changed = cursor.rowcount
            cursor.execute('SELECT id FROM api_tokens WHERE id=%s', (token_id,))
            exists = cursor.fetchone() is not None
        connection.commit()
    if not exists:
        raise ValueError('Token ID does not exist')
    return bool(changed)


def migrate(settings, admin_socket=None):
    """Resumable additive migration, including MySQL versions without ADD IF NOT EXISTS."""
    from app.config import ROOT
    connection = (pymysql.connect(unix_socket=admin_socket, user=getpass.getuser(),
                                  database=settings.db_name, charset='utf8mb4',
                                  connect_timeout=5, read_timeout=30, write_timeout=30)
                  if admin_socket else connect(settings))
    with connection:
        with connection.cursor() as cursor:
            cursor.execute((ROOT / 'sql/007_api_tokens.sql').read_text())
            cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
                           "AND TABLE_NAME='audits' AND COLUMN_NAME='api_token_id'", (settings.db_name,))
            if not cursor.fetchone()[0]:
                cursor.execute('ALTER TABLE audits ADD COLUMN api_token_id BIGINT UNSIGNED NULL, '
                               'ADD INDEX idx_audits_api_token (api_token_id)')
        connection.commit()


def main(settings, arguments):
    parser = argparse.ArgumentParser(description='Administrator-only token management via server shell')
    sub = parser.add_subparsers(dest='action', required=True)
    migration = sub.add_parser('migrate', help='Create token table and add audit attribution column')
    migration.add_argument('--admin-socket', help='Use current OS user via a local administrator MySQL socket')
    for action in ('create', 'import-legacy'):
        command = sub.add_parser(action)
        command.add_argument('--name', required=True)
        command.add_argument('--note', default='')
    sub.add_parser('list')
    command = sub.add_parser('revoke')
    command.add_argument('--id', type=int, required=True)
    args = parser.parse_args(arguments)
    try:
        if args.action == 'migrate':
            migrate(settings, args.admin_socket)
            result = {'migration': '007_api_tokens', 'status': 'ready'}
        elif args.action in ('create', 'import-legacy'):
            legacy = settings.api_token.get_secret_value() if args.action == 'import-legacy' else None
            if legacy == '':
                raise ValueError('No legacy API_TOKEN configured')
            token_id, value = create_token(settings, args.name, args.note, legacy=legacy)
            result = {'id': token_id, 'name': args.name, 'expires': 'never'}
            if legacy is None:
                result['token'] = value
                result['notice'] = 'Save now. This token cannot be retrieved again.'
            else:
                result['notice'] = 'Existing API_TOKEN imported. Its value is not displayed.'
        elif args.action == 'list':
            result = {'tokens': list_tokens(settings)}
        else:
            result = {'id': args.id, 'status': 'revoked', 'changed': revoke_token(settings, args.id)}
    except pymysql.IntegrityError:
        raise ValueError('Token already exists; revoked tokens cannot be re-imported') from None
    except pymysql.MySQLError:
        raise ValueError('Database operation failed. Check database availability, permissions and tokens migrate.') from None
    print(json.dumps({'environment': settings.app_env, **result}, ensure_ascii=False, indent=2))
