import datetime
from rest_framework import serializers
from .models import Task


class TaskSerializer(serializers.ModelSerializer):
    is_subtask = serializers.BooleanField(read_only=True)
    subtask_count = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'completed',
            'due_date', 'priority', 'recurrence',
            'parent', 'is_subtask', 'subtask_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'is_subtask', 'subtask_count']

    def get_subtask_count(self, obj):
        return obj.subtasks.count()

    def validate_title(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('must not be empty')
        return value.strip()

    def validate_description(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('must not be empty')
        return value.strip()

    def validate_priority(self, value):
        allowed = [c[0] for c in Task.PRIORITY_CHOICES]
        if value not in allowed:
            raise serializers.ValidationError(
                f"must be one of: {', '.join(allowed)}"
            )
        return value

    def validate_due_date(self, value):
        """Guard against impossible dates (e.g. from the NL parser)."""
        if value is None:
            return value
        if isinstance(value, str):
            try:
                value = datetime.date.fromisoformat(value)
            except ValueError:
                raise serializers.ValidationError('must be a real YYYY-MM-DD date')
        return value
