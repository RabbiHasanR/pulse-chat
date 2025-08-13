import pytest
import jwt
from unittest.mock import patch
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from users.models import Contact, ChatUser
from tests.constants import *
from utils.auth_util import generate_email_token
from utils.jwt_util import issue_token_for_user

# --- RegisterUserView Tests ---

@pytest.mark.django_db
@patch("users.views.send_templated_email_task.delay")
def test_register_user_success(mock_send_email):
    client = APIClient()
    response = client.post(REGISTER_URL, {
        "email": DUMMY_EMAIL,
        "username": DUMMY_USERNAME,
        "full_name": DUMMY_NAME,
        "password": DUMMY_PASSWORD,
    })
    assert response.status_code == 201
    assert response.data["success"] is True
    assert response.data["message"] == "User registered"
    
    mock_send_email.assert_called_once_with(
        subject="Welcome to Our Platform!",
        to_email=DUMMY_EMAIL,
        template_name="emails/welcome_email.html",
        context={"user_email": DUMMY_EMAIL}
    )

@pytest.mark.django_db
def test_register_user_invalid_data():
    client = APIClient()
    response = client.post(REGISTER_URL, {"email": "invalid"})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert "email" in response.data["errors"]
    assert "username" in response.data["errors"]
    assert "full_name" in response.data["errors"]

# --- SendOTPView Tests ---

@pytest.mark.django_db
@patch("users.views.send_templated_email_task.delay")
def test_send_otp_success(mock_send_otp, user):
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": user.email})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "token" in response.data["data"]
    
    cached_otp = cache.get(f"otp_{user.email}")
    assert cached_otp is not None
    
    mock_send_otp.assert_called_once_with(
        subject="Your OTP Code",
        to_email=user.email,
        template_name="emails/otp_email.html",
        context={"otp": cached_otp, "user_email": user.email}
    )

@pytest.mark.django_db
def test_send_otp_user_not_found():
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": "notfound@example.com"})
    assert response.status_code == 404
    assert response.data["success"] is False
    assert response.data["message"] == "User not found"

# --- VerifyOTPView Tests ---

@pytest.mark.django_db
def test_verify_otp_success(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": otp})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "access" in response.data["data"]
    assert "refresh" in response.data["data"]

@pytest.mark.django_db
def test_verify_otp_invalid_otp(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": "000000"})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert response.data["message"] == "Invalid OTP"

@pytest.mark.django_db
def test_verify_otp_expired_token():
    expired_token = jwt.encode(
        {"email": DUMMY_EMAIL, "exp": 0},
        settings.SECRET_KEY,
        algorithm="HS256"
    )
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": expired_token, "otp": "123456"})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert response.data["message"] == "Token expired"

# --- CustomTokenRefreshView Tests ---

@pytest.mark.django_db
def test_refresh_token_success(issue_bound_token, mock_request):
    client = APIClient()
    ip = mock_request().META.get('REMOTE_ADDR', '')
    ua = mock_request().META.get('HTTP_USER_AGENT', '')
    response = client.post(
        REFRESH_URL,
        {"refresh": str(issue_bound_token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "access" in response.data["data"]
    assert "refresh" in response.data["data"]

@pytest.mark.django_db
def test_refresh_token_missing():
    client = APIClient()
    response = client.post(REFRESH_URL, {})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert response.data["message"] == "Missing refresh token"

@pytest.mark.django_db
def test_refresh_token_client_mismatch(user, mock_request):
    original = mock_request("192.168.1.1", "UA")
    altered = mock_request("10.0.0.1", "UA")
    token = issue_token_for_user(user, original)
    client = APIClient()
    ip = altered.META.get('REMOTE_ADDR', '')
    ua = altered.META.get('HTTP_USER_AGENT', '')
    response = client.post(
        REFRESH_URL,
        {"refresh": str(token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 403
    assert response.data["success"] is False
    assert response.data["message"] == "Client mismatch"

@pytest.mark.django_db
def test_refresh_token_blacklisted(issue_bound_token):
    issue_bound_token.blacklist()
    client = APIClient()
    response = client.post(REFRESH_URL, {"refresh": str(issue_bound_token)})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert response.data["message"] == "Invalid token"

# --- LogoutView Tests ---

@pytest.mark.django_db
def test_logout_success(auth_client, issue_bound_token):
    response = auth_client.post(LOGOUT_URL, {"refresh": str(issue_bound_token)})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert response.data["message"] == "Logout successful"
    assert BlacklistedToken.objects.filter(token__jti=issue_bound_token["jti"]).exists()

@pytest.mark.django_db
def test_logout_missing_token(auth_client):
    response = auth_client.post(LOGOUT_URL, {})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert "refresh" in response.data["errors"]

@pytest.mark.django_db
def test_logout_invalid_token(auth_client):
    response = auth_client.post(LOGOUT_URL, {"refresh": "malformed.token.string"})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert "refresh" in response.data["errors"]

@pytest.mark.django_db
def test_logout_client_mismatch(user, mock_request):
    original = mock_request("192.168.1.1", "UA")
    altered = mock_request("10.0.0.1", "UA")
    token = issue_token_for_user(user, original)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token.access_token)}")
    ip = altered.META.get('REMOTE_ADDR', '')
    ua = altered.META.get('HTTP_USER_AGENT', '')
    response = client.post(
        LOGOUT_URL,
        {"refresh": str(token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 403
    assert response.data["success"] is False
    assert response.data["errors"]["token"][0] == "Token does not match client signature"




# --- AddContactView Tests ---

@pytest.mark.django_db
def test_add_contact_by_email_success(auth_client, another_user):
    response = auth_client.post(ADD_CONTACTS_URL, {"identifier": another_user.email})
    assert response.status_code == 201
    assert response.data["success"] is True
    assert response.data["data"]["email"] == another_user.email
    assert Contact.objects.filter(contact_user=another_user).exists()


@pytest.mark.django_db
def test_add_contact_by_username_success(auth_client, another_user):
    response = auth_client.post(ADD_CONTACTS_URL, {"identifier": another_user.username})
    assert response.status_code == 201
    assert response.data["data"]["username"] == another_user.username
    assert Contact.objects.filter(contact_user=another_user).exists()

@pytest.mark.django_db
def test_add_contact_already_exists(auth_client, user, another_user):
    Contact.objects.create(owner=user, contact_user=another_user)
    response = auth_client.post(ADD_CONTACTS_URL, {"identifier": another_user.email})
    assert response.status_code == 200
    assert response.data["message"] == "Already in contacts"

@pytest.mark.django_db
def test_add_contact_self(auth_client, user):
    response = auth_client.post(ADD_CONTACTS_URL, {"identifier": user.email})
    assert response.status_code == 400
    assert "Self-addition is not allowed" in str(response.data["errors"]["identifier"])

@pytest.mark.django_db
def test_add_contact_user_not_found(auth_client):
    response = auth_client.post(ADD_CONTACTS_URL, {"identifier": "nonexistent@example.com"})
    assert response.status_code == 404
    assert "User not found" in response.data["message"]

@pytest.mark.django_db
def test_add_contact_missing_identifier(auth_client):
    response = auth_client.post(ADD_CONTACTS_URL, {})
    assert response.status_code == 400
    assert "This field is required" in str(response.data["errors"]["identifier"])
    
    
    
# --- GetContactsView Tests ---

@pytest.mark.django_db
def test_get_contacts_success(auth_client, user, another_user):
    Contact.objects.create(owner=user, contact_user=another_user)

    response = auth_client.get(GET_CONTACTS_URL)

    assert response.status_code == 200
    assert response.data["success"] is True
    assert "contacts" in response.data["data"]
    assert len(response.data["data"]["contacts"]) == 1
    assert response.data["data"]["contacts"][0]["contact_user"]["email"] == another_user.email
    

@pytest.mark.django_db
def test_get_contacts_empty(auth_client):
    response = auth_client.get(GET_CONTACTS_URL)

    assert response.status_code == 200
    assert response.data["success"] is True
    assert response.data["data"]["contacts"] == []
    

@pytest.mark.django_db
def test_get_contacts_unauthenticated():
    client = APIClient()
    response = client.get(GET_CONTACTS_URL)

    assert response.status_code == 401
    
@pytest.mark.django_db
def test_get_contacts_internal_error(auth_client):
    with patch("contacts.models.Contact.objects.filter") as mock_filter:
        mock_filter.side_effect = Exception("Simulated failure")

        response = auth_client.get(GET_CONTACTS_URL)

        assert response.status_code == 500
        assert response.data["success"] is False
        assert response.data["message"] == "Failed to retrieve contacts"
        assert "Simulated failure" in response.data["errors"]
        


@pytest.mark.django_db
def test_get_contacts_pagination(auth_client, user):
    for i in range(15):
        contact_user = ChatUser.objects.create_user(
            email=f"user{i}@example.com",
            username=f"user{i}",
            full_name=f"User {i}",
            password="testpass"
        )
        Contact.objects.create(owner=user, contact_user=contact_user)

    response = auth_client.get(GET_CONTACTS_URL)

    assert response.status_code == 200
    assert response.data["success"] is True

    contacts = response.data["data"]["contacts"]
    next_link = response.data["data"]["next"]

    assert len(contacts) == 10
    assert next_link is not None

    next_response = auth_client.get(next_link)

    assert next_response.status_code == 200
    assert next_response.data["success"] is True
    assert len(next_response.data["data"]["contacts"]) == 5
    assert next_response.data["data"]["previous"] is not None