from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.utils import timezone
from crp_accounting.models.period import FiscalYear, AccountingPeriod
from crp_accounting.serializers.period import FiscalYearSerializer, AccountingPeriodSerializer


class FiscalYearViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing fiscal years.
    Enforces immutability on closed years and allows soft-close logic.
    """
    queryset = FiscalYear.objects.all().order_by('-start_date')
    serializer_class = FiscalYearSerializer

    @action(detail=True, methods=['post'])
    def close_year(self, request, pk=None):
        """
        Endpoint to soft-close a fiscal year, preventing further edits or period creation.
        Only allowed if all accounting periods inside the year are locked.
        """
        fiscal_year = self.get_object()

        if fiscal_year.status == 'Closed':
            return Response({'detail': 'Fiscal year already closed.'}, status=status.HTTP_400_BAD_REQUEST)

        open_periods = AccountingPeriod.objects.filter(fiscal_year=fiscal_year, is_locked=False)
        if open_periods.exists():
            return Response({'detail': 'Cannot close fiscal year. Some periods are still unlocked.'},
                            status=status.HTTP_400_BAD_REQUEST)

        fiscal_year.status = 'Closed'
        fiscal_year.closed_by = request.user
        fiscal_year.closed_at = timezone.now()
        fiscal_year.save()

        return Response({'detail': 'Fiscal year successfully closed.'})


class AccountingPeriodViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing accounting periods.
    Ensures validation of period range within fiscal year and lock behavior.
    """
    queryset = AccountingPeriod.objects.select_related('fiscal_year').all().order_by('-start_date')
    serializer_class = AccountingPeriodSerializer

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        """
        Lock an accounting period. Locked periods cannot be edited or used for future entries.
        """
        period = self.get_object()
        if period.is_locked:
            return Response({'detail': 'Accounting period is already locked.'}, status=status.HTTP_400_BAD_REQUEST)

        period.is_locked = True
        period.save()
        return Response({'detail': 'Accounting period locked successfully.'})
