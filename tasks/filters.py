import django_filters
from .models import Task


class TaskFilter(django_filters.FilterSet):
    completed = django_filters.BooleanFilter()
    priority = django_filters.ChoiceFilter(choices=Task.PRIORITY_CHOICES)
    due_date = django_filters.DateFilter()
    due_date_after = django_filters.DateFilter(field_name='due_date', lookup_expr='gte')
    due_date_before = django_filters.DateFilter(field_name='due_date', lookup_expr='lte')
    # ?has_parent=false  → top-level tasks only
    # ?has_parent=true   → subtasks only
    has_parent = django_filters.BooleanFilter(
        field_name='parent', lookup_expr='isnull', exclude=True
    )

    class Meta:
        model = Task
        fields = ['completed', 'priority', 'due_date', 'parent']
