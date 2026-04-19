from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import ChatUser, Contact


@admin.register(ChatUser)
class ChatUserAdmin(UserAdmin):
    list_display = ('email', 'username', 'full_name', 'is_active', 'is_admin', 'is_staff', 'date_joined')
    list_filter = ('is_active', 'is_admin', 'is_staff')
    search_fields = ('email', 'username', 'full_name')
    ordering = ('-date_joined',)
    fieldsets = UserAdmin.fieldsets + (
        ('Profile', {'fields': ('full_name', 'is_admin', 'avatar_bucket', 'avatar_key')}),
    )


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('owner', 'contact_user', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('owner__email', 'contact_user__email')
    ordering = ('-created_at',)
    raw_id_fields = ('owner', 'contact_user')
