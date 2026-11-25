import datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .ai import break_down_task, parse_task_text
from .filters import TaskFilter
from .idempotency import check as idem_check, store as idem_store
from .models import Task
from .serializers import TaskSerializer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def error_response(code: str, message: str, details=None,
                   http_status=status.HTTP_400_BAD_REQUEST):
    """Single factory for every error shape returned by this API."""
    return Response(
        {
            'error': {
                'code': str(code),
                'message': str(message),
                'details': details or [],
            }
        },
        status=http_status,
    )


def flatten_serializer_errors(errors: dict) -> list:
    """Convert DRF's nested error dict into our flat details list."""
    details = []
    for field, issues in errors.items():
        if isinstance(issues, list):
            for issue in issues:
                details.append({'field': field, 'issue': str(issue)})
        elif isinstance(issues, dict):
            for sub_field, sub_issues in issues.items():
                for issue in (sub_issues if isinstance(sub_issues, list) else [sub_issues]):
                    details.append({'field': f'{field}.{sub_field}', 'issue': str(issue)})
    return details


def handle_idempotency(request, body: dict):
    """
    Checks the Idempotency-Key header.

    Returns:
        (key, None)           – new key, caller should proceed
        (key, early_response) – cached match or conflict, caller should return early_response
        (None, None)          – no header, skip idempotency entirely
    """
    key = request.headers.get('Idempotency-Key')
    if not key:
        return None, None

    result = idem_check(key, body)
    if result is None:
        return key, None

    kind, stored_response, stored_status = result
    if kind == 'match':
        return key, Response(stored_response, status=stored_status)

    return key, error_response(
        'IDEMPOTENCY_CONFLICT',
        'The same Idempotency-Key was sent with a different request body.',
        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


# ---------------------------------------------------------------------------
# ViewSet
# ---------------------------------------------------------------------------

class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = TaskFilter
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'due_date', 'priority', 'title']
    ordering = ['-created_at']

    def get_queryset(self):
        # List/filter top-level tasks only by default.
        # Subtasks are accessed via /api/tasks/{id}/subtasks/.
        return Task.objects.filter(parent__isnull=True)

    # ------------------------------------------------------------------
    # Standard CRUD – overridden for consistent error shape
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        body = dict(request.data)
        idem_key, early = handle_idempotency(request, body)
        if early is not None:
            return early

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                'VALIDATION_ERROR', 'Validation failed',
                details=flatten_serializer_errors(serializer.errors),
            )
        self.perform_create(serializer)
        response_data = serializer.data

        if idem_key:
            idem_store(idem_key, body, response_data, status.HTTP_201_CREATED)

        return Response(response_data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = Task.objects.get(pk=kwargs['pk'])
        except Task.DoesNotExist:
            return error_response(
                'NOT_FOUND', f"Task {kwargs['pk']} not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        try:
            instance = Task.objects.get(pk=kwargs['pk'])
        except Task.DoesNotExist:
            return error_response(
                'NOT_FOUND', f"Task {kwargs['pk']} not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        if not serializer.is_valid():
            return error_response(
                'VALIDATION_ERROR', 'Validation failed',
                details=flatten_serializer_errors(serializer.errors),
            )
        self.perform_update(serializer)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = Task.objects.get(pk=kwargs['pk'])
        except Task.DoesNotExist:
            return error_response(
                'NOT_FOUND', f"Task {kwargs['pk']} not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='from-text')
    def from_text(self, request):
        """POST /api/tasks/from-text/ — create a task from plain English."""
        body = dict(request.data)
        idem_key, early = handle_idempotency(request, body)
        if early is not None:
            return early

        text = request.data.get('text', '')
        if not text or not str(text).strip():
            return error_response(
                'VALIDATION_ERROR', 'Validation failed',
                details=[{'field': 'text', 'issue': 'must not be empty'}],
            )

        # Step 1: parse plain English
        try:
            parsed = parse_task_text(str(text), today=datetime.date.today())
        except Exception as exc:
            return error_response(
                'PARSER_ERROR', f'Failed to parse task text: {exc}',
                http_status=status.HTTP_502_BAD_GATEWAY,
            )

        # Step 2: never trust parser output – validate it first
        serializer = self.get_serializer(data=parsed)
        if not serializer.is_valid():
            return error_response(
                'PARSER_OUTPUT_INVALID',
                'Parser returned data that failed validation.',
                details=flatten_serializer_errors(serializer.errors),
                http_status=status.HTTP_502_BAD_GATEWAY,
            )

        serializer.save()
        response_data = serializer.data

        if idem_key:
            idem_store(idem_key, body, response_data, status.HTTP_201_CREATED)

        return Response(response_data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='subtasks')
    def subtasks(self, request, pk=None):
        """GET  /api/tasks/{id}/subtasks/ — list subtasks
           POST /api/tasks/{id}/subtasks/ — create a subtask"""
        try:
            parent = Task.objects.get(pk=pk)
        except Task.DoesNotExist:
            return error_response(
                'NOT_FOUND', f'Task {pk} not found.',
                http_status=status.HTTP_404_NOT_FOUND,
            )

        if parent.is_subtask:
            return error_response(
                'FORBIDDEN', 'A subtask cannot have its own subtasks.'
            )

        if request.method == 'GET':
            serializer = self.get_serializer(parent.subtasks.all(), many=True)
            return Response(serializer.data)

        # POST – create one subtask
        data = request.data.copy()
        data['parent'] = parent.pk
        serializer = self.get_serializer(data=data)
        if not serializer.is_valid():
            return error_response(
                'VALIDATION_ERROR', 'Validation failed',
                details=flatten_serializer_errors(serializer.errors),
            )
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='breakdown')
    def breakdown(self, request, pk=None):
        """POST /api/tasks/{id}/breakdown/ — auto-generate subtasks."""
        try:
            task = Task.objects.get(pk=pk)
        except Task.DoesNotExist:
            return error_response(
                'NOT_FOUND', f'Task {pk} not found.',
                http_status=status.HTTP_404_NOT_FOUND,
            )

        if task.is_subtask:
            return error_response('FORBIDDEN', 'A subtask cannot be broken down.')

        idem_key, early = handle_idempotency(request, {'task_id': str(pk)})
        if early is not None:
            return early

        try:
            subtask_dicts = break_down_task(task)
        except Exception as exc:
            return error_response(
                'BREAKDOWN_ERROR', str(exc),
                http_status=status.HTTP_502_BAD_GATEWAY,
            )

        # Validate ALL items before saving ANY (atomic behaviour)
        validated_serializers = []
        for item in subtask_dicts:
            item['parent'] = task.pk
            s = self.get_serializer(data=item)
            if not s.is_valid():
                return error_response(
                    'VALIDATION_ERROR',
                    'Breakdown produced invalid subtask data.',
                    details=flatten_serializer_errors(s.errors),
                )
            validated_serializers.append(s)

        created = []
        for s in validated_serializers:
            s.save()
            created.append(s.data)

        if idem_key:
            idem_store(idem_key, {'task_id': str(pk)}, created, status.HTTP_201_CREATED)

        return Response(created, status=status.HTTP_201_CREATED)
