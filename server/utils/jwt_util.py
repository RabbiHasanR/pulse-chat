from rest_framework_simplejwt.tokens import RefreshToken

def get_client_signature(request):
    ip = request.META.get('REMOTE_ADDR', '')
    ua = request.META.get('HTTP_USER_AGENT', '')
    return hash(f'{ip}-{ua}')

def issue_token_for_user(user, request):
    token = RefreshToken.for_user(user)
    token['client_hash'] = str(get_client_signature(request))
    return token

def verify_token_signature(token, request):
    try:
        client_hash = token['client_hash']
        expected_hash = str(get_client_signature(request))
        return client_hash == expected_hash
    except KeyError:
        return False
