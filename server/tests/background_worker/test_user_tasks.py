from unittest.mock import patch, MagicMock
from background_worker.users.tasks import send_templated_email_task

@patch("background_worker.users.tasks.EmailMultiAlternatives")
@patch("background_worker.users.tasks.render_to_string")
def test_send_templated_email_task(mock_render, mock_email_class):
    mock_render.side_effect = ["<html>Email</html>", "Plain text email"]
    mock_email = MagicMock()
    mock_email_class.return_value = mock_email

    send_templated_email_task(
        subject="Welcome!",
        to_email="test@example.com",
        template_name="emails/welcome_email.html",
        context={"user_email": "test@example.com"}
    )

    mock_render.assert_any_call("emails/welcome_email.html", {"user_email": "test@example.com"})
    mock_render.assert_any_call("emails/welcome_email.txt", {"user_email": "test@example.com"})

    mock_email.attach_alternative.assert_called_once_with("<html>Email</html>", "text/html")
    mock_email.send.assert_called_once()
