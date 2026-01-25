from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser


class ChatUser(AbstractUser):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    is_admin = models.BooleanField(default=False)
    
    # Store S3 metadata instead of hardcoded URL
    avatar_bucket = models.CharField(max_length=100, blank=True, null=True)
    avatar_key = models.CharField(max_length=255, blank=True, null=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'full_name']

    @property
    def avatar_url(self):
        """
        Dynamically constructs the S3 URL based on the environment.
        Returns None if key or bucket is missing.
        """
        if not self.avatar_bucket or not self.avatar_key:
            return None

        # 1. LOCAL DEV (S3 MOCK)
        # If we are using the mock, we must serve from localhost, not AWS.
        if settings.USE_S3_MOCK:
            # Moto/S3Mock usually runs on port 5000.
            # Format: http://localhost:5000/{bucket}/{key}
            return f"http://localhost:5000/{self.avatar_bucket}/{self.avatar_key}"

        # 2. PRODUCTION (AWS S3)
        # Standard S3 URL format.
        return f"https://{self.avatar_bucket}.s3.amazonaws.com/{self.avatar_key}"



class Contact(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='contacts', on_delete=models.CASCADE)
    contact_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='added_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('owner', 'contact_user')

    def __str__(self):
        return f'{self.owner.email} → {self.contact_user.email}'