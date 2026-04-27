from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'azione', 'target_type', 'target_label', 'actor_label', 'ip')
    list_filter = ('azione', 'target_type')
    search_fields = ('actor_label', 'target_label', 'target_id')
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in AuditLog._meta.fields]
