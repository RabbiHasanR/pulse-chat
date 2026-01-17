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
        Dynamically constructs the S3 URL.
        Returns None if key or bucket is missing.
        """
        if self.avatar_bucket and self.avatar_key:
            # Standard S3 URL format. 
            # If you use a custom domain or CloudFront, change this string.
            return f"https://{self.avatar_bucket}.s3.amazonaws.com/{self.avatar_key}"
        return None



class Contact(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='contacts', on_delete=models.CASCADE)
    contact_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='added_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('owner', 'contact_user')

    def __str__(self):
        return f'{self.owner.email} → {self.contact_user.email}'