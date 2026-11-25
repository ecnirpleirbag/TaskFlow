from django.contrib import admin
from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'priority', 'completed', 'due_date', 'parent', 'created_at')
    list_filter = ('priority', 'completed')
    search_fields = ('title', 'description')
    readonly_fields = ('created_at', 'updated_at')
