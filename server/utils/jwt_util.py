from rest_framework_simplejwt.tokens import RefreshToken

def get_client_signature(request):
    ip = request.META.get('REMOTE_ADDR', '')
    ua = request.META.get('HTTP_USER_AGENT', '')
    return hash(f'{ip}-{ua}')

def issue_token_for_user(user, request):
    refresh = RefreshToken.for_user(user)
    client_hash = str(get_client_signature(request))

    refresh['client_hash'] = client_hash
    refresh.access_token['client_hash'] = client_hash
    return refresh

def verify_token_signature(token, request):
    try:
        client_hash = token['client_hash']
        expected_hash = str(get_client_signature(request))
        return client_hash == expected_hash
    except KeyError:
        return False
