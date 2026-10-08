"""Windows Credential Manager / OS keyring. Configuration only stores references."""
import os
import hashlib
from django.conf import settings

def service_name():
    # Isolate separate installations on the same Windows account.
    identity = hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:24]
    return 'zhishiku-platform-' + identity


def read_secret(reference):
    if reference.startswith('env:'):
        return os.environ.get(reference[4:], '')
    if reference.startswith('credential:'):
        import keyring
        try:
            return keyring.get_password(service_name(), reference[11:]) or ''
        except keyring.errors.KeyringError:
            return ''
    return reference  # Compatibility read; migrate_secrets moves legacy values explicitly.


def store_secret(name, value):
    if value.startswith(('env:', 'credential:')):
        return value
    import keyring
    try:
        keyring.set_password(service_name(), name, value)
    except keyring.errors.KeyringError as exc:
        from django.core.exceptions import ValidationError
        raise ValidationError('系统凭据存储不可用，请使用 env:变量名 配置；未保存明文密钥') from exc
    return 'credential:' + name
