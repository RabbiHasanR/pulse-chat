import pytest
import json
from unittest.mock import patch, Mock
from rest_framework.test import APIRequestFactory
from middlewares.auth_middleware import JWTClientBindingMiddleware

factory = APIRequestFactory()



def test_valid_token_and_matching_signature(issue_bound_token, mock_request, get_response):
    request = factory.get("/")
    request.headers = {
        "Authorization": f"Bearer {str(issue_bound_token.access_token)}"
    }
    request.META = mock_request().META

    with patch("middlewares.auth_middleware.AccessToken", return_value=issue_bound_token.access_token), \
         patch("middlewares.auth_middleware.verify_token_signature", return_value=True):
        middleware = JWTClientBindingMiddleware(get_response)
        response = middleware(request)
        assert response.status_code == 200

def test_valid_token_but_signature_mismatch(issue_bound_token, mock_request, get_response):
    request = factory.get("/")
    request.headers = {
        "Authorization": f"Bearer {str(issue_bound_token.access_token)}"
    }
    request.META = mock_request().META

    with patch("middlewares.auth_middleware.AccessToken", return_value=issue_bound_token.access_token), \
         patch("middlewares.auth_middleware.verify_token_signature", return_value=False):
        middleware = JWTClientBindingMiddleware(get_response)
        response = middleware(request)
        assert response.status_code == 403
        body = json.loads(response.content)
        assert body["message"] == "Client mismatch"

def test_invalid_token(get_response):
    request = factory.get("/")
    request.headers = {
        "Authorization": "Bearer invalid.token.string"
    }

    with patch("middlewares.auth_middleware.AccessToken", side_effect=Exception("Invalid token")):
        middleware = JWTClientBindingMiddleware(get_response)
        response = middleware(request)
        assert response.status_code == 401
        body = json.loads(response.content)
        assert body["message"] == "Invalid token"

def test_missing_authorization_header(get_response):
    request = factory.get("/")
    request.headers = {}

    middleware = JWTClientBindingMiddleware(get_response)
    response = middleware(request)
    assert response.status_code == 200

def test_authorization_header_without_bearer(get_response):
    request = factory.get("/")
    request.headers = {
        "Authorization": "Token abc.def.ghi"
    }

    middleware = JWTClientBindingMiddleware(get_response)
    response = middleware(request)
    assert response.status_code == 200
