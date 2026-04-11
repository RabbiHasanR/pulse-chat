import hashlib

from rest_framework_simplejwt.tokens import RefreshToken


def get_client_signature(request) -> str:
    ip = request.META.get('REMOTE_ADDR', '')
    ua = request.META.get('HTTP_USER_AGENT', '')
    return hashlib.sha256(f'{ip}-{ua}'.encode()).hexdigest()


def issue_token_for_user(user, request) -> RefreshToken:
    refresh = RefreshToken.for_user(user)
    client_hash = get_client_signature(request)

    refresh['client_hash'] = client_hash
    refresh.access_token['client_hash'] = client_hash
    return refresh


def verify_token_signature(token, request) -> bool:
    try:
        client_hash = token['client_hash']
        expected_hash = get_client_signature(request)
        return client_hash == expected_hash
    except KeyError:
        return False
