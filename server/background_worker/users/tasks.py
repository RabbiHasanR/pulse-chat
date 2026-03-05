from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
import smtplib

@shared_task(
    bind=True, 
    acks_late=True,
    soft_time_limit=15,
    time_limit=20,
    max_retries=3,
    autoretry_for=(smtplib.SMTPException, TimeoutError, ConnectionError),
    retry_backoff=True
)
def send_templated_email_task(self, subject, to_email, template_name, context):
    try:
        from_email = settings.DEFAULT_FROM_EMAIL

        html_content = render_to_string(template_name, context)
        text_content = render_to_string(template_name.replace(".html", ".txt"), context)

        email = EmailMultiAlternatives(subject, text_content, from_email, [to_email])
        email.attach_alternative(html_content, "text/html")
        email.send()
        
    except SoftTimeLimitExceeded:
        raise

    except Exception as exc:
        raise
    
    
    
    
    
    
