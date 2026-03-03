from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
import smtplib # For catching email connection errors

@shared_task(
    bind=True, 
    # Queue is omitted, so it defaults to 'default'
    acks_late=True,
    soft_time_limit=15, # 15 seconds! If SMTP hangs, kill it quickly
    time_limit=20,
    max_retries=3,
    autoretry_for=(smtplib.SMTPException, TimeoutError, ConnectionError),
    retry_backoff=True # If it fails, wait 1s, then 2s, then 4s before retrying
)
def send_templated_email_task(self, subject, to_email, template_name, context):
    try:
        from_email = settings.DEFAULT_FROM_EMAIL

        html_content = render_to_string(template_name, context)
        text_content = render_to_string(template_name.replace(".html", ".txt"), context)

        email = EmailMultiAlternatives(subject, text_content, from_email, [to_email])
        email.attach_alternative(html_content, "text/html")
        email.send()
        
    except Exception as exc:
        # If an unexpected error happens, trigger the retry logic
        raise self.retry(exc=exc)