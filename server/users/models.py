from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser


class ChatUser(AbstractUser):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    is_admin = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'full_name']



class Contact(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='contacts', on_delete=models.CASCADE)
    contact_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='added_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('owner', 'contact_user')

    def __str__(self):
        return f'{self.owner.email} → {self.contact_user.email}'