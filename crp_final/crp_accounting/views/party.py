# crp_accounting/views/party.py

import logging
from decimal import Decimal, InvalidOperation
from datetime import date

from django.db.models import ProtectedError  # Importing only what's necessary
from django.utils import timezone
from rest_framework import viewsets, permissions, status, filters
from rest_framework.exceptions import ValidationError, ParseError
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from django.utils.translation import gettext_lazy as _
# --- Swagger/Spectacular Imports ---
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiParameter, OpenApiTypes, OpenApiResponse, inline_serializer
)

# Adjust project-specific imports
from crp_accounting.models import Party, Account
from crp_accounting.models.journal import Voucher  # Needed for deletion check
from crp_accounting.serializers.party import (
    PartyReadSerializer,
    PartyWriteSerializer
)
from crp_core.enums import PartyType
from django.conf import settings  # For optional toggle

logger = logging.getLogger(__name__)


# --- Standard Pagination (if not global) ---
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 1000


# --- Party ViewSet ---
@extend_schema_view(
    list=extend_schema(
        summary="List Parties",
        description="Retrieve a paginated list of parties (customers, suppliers, etc.), supporting filtering and searching. Balance information is calculated dynamically.",
    ),
    retrieve=extend_schema(
        summary="Retrieve Party",
        description="Get details of a specific party, including dynamically calculated balance and credit status."
    ),
    create=extend_schema(
        summary="Create Party",
        description="Create a new party, ensuring a valid control account is linked for active customers/suppliers."
    ),
    update=extend_schema(
        summary="Update Party",
        description="Update an existing party completely."
    ),
    partial_update=extend_schema(
        summary="Partial Update Party",
        description="Update parts of an existing party."
    ),
    destroy=extend_schema(
        summary="Delete Party",
        description="Delete a party. **Important:** Deletion is blocked if the party has any associated journal entries.",
        responses={
            204: OpenApiResponse(description="Party successfully deleted."),
            400: OpenApiResponse(description="Deletion failed (e.g., transactions exist)."),
        }
    ),
    # Custom Action Schemas...
)
class PartyViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing Parties (Customers, Suppliers, etc.).

    Handles CRUD operations and includes accounting-specific logic like
    deletion prevention based on transactions and dynamic balance display.
    """
    queryset = Party.objects.select_related('control_account').all()
    permission_classes = [permissions.IsAuthenticated]  # Adapt as needed
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    # Define filterable fields
    filterset_fields = {
        'party_type': ['exact'],
        'is_active': ['exact'],
        'control_account': ['exact'],
        'control_account__account_name': ['exact', 'icontains'],
        'name': ['icontains'],
        'contact_email': ['icontains'],
    }
    # Define searchable fields
    search_fields = [
        'name', 'contact_email', 'contact_phone',
        'control_account__account_number', 'control_account__account_name'
    ]
    # Define orderable fields
    ordering_fields = ['name', 'party_type', 'is_active', 'created_at', 'control_account__name']
    ordering = ['name']  # Default ordering

    def get_serializer_class(self):
        """Switch between Read and Write serializers."""
        if self.action in ['list', 'retrieve']:
            return PartyReadSerializer
        return PartyWriteSerializer

    def perform_destroy(self, instance: Party):
        """
        **Accounting Logic:** Prevents deletion if the party has associated journal entries.
        """
        if Voucher.objects.filter(party=instance).exists():
            logger.warning(
                f"Attempted to delete Party '{instance.name}' (ID: {instance.id}) which has associated Journal Entries.")
            raise ValidationError(
                f"Cannot delete party '{instance.name}' because it has financial transactions recorded. "
                "Consider making the party inactive instead."
            )

        try:
            logger.info(f"Deleting Party '{instance.name}' (ID: {instance.id}).")
            super().perform_destroy(instance)
        except ProtectedError as e:
            logger.error(
                f"Deletion failed for Party '{instance.name}' (ID: {instance.id}) due to protected relationships: {e}")
            raise ValidationError(f"Deletion failed. This party might be linked to other records.")

    # --- Custom Actions ---
    @action(detail=True, methods=['get'], url_path='balance-as-of')
    def balance_as_of(self, request, pk=None):
        """Calculates and returns the party's balance as of a specific date."""
        party = self.get_object()  # Gets the party instance
        date_str = request.query_params.get('date', None)
        if not date_str:
            raise ParseError(detail=_("Missing 'date' query parameter (YYYY-MM-DD)."))
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise ParseError(detail=_("Invalid date format for 'date'. Use YYYY-MM-DD."))

        try:
            balance = party.calculate_outstanding_balance(date_upto=target_date)
            return Response({'party_id': party.id, 'date_as_of': target_date, 'balance': balance})
        except ValueError as e:  # Catch potential errors from calculation
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='check-credit')
    def check_credit_limit(self, request, pk=None):
        """Checks if a potential transaction amount would exceed the credit limit."""
        party = self.get_object()
        amount_str = request.query_params.get('amount', None)

        if amount_str is None:
            raise ParseError(detail=_("Missing 'amount' query parameter."))
        try:
            transaction_amount = Decimal(amount_str)
            if transaction_amount <= 0:
                raise ValueError("Amount must be positive.")
        except (InvalidOperation, ValueError):
            raise ParseError(detail=_("Invalid 'amount' provided. Must be a positive number."))

        try:
            party.check_credit_limit(transaction_amount)
            return Response({
                'status': 'OK',
                'message': _("Amount is within the credit limit.")
            })
        except ValidationError as e:
            return Response({
                'status': 'Exceeded',
                'message': e.detail[0] if isinstance(e.detail, list) else str(e.detail)
            }, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:  # Catch calculation errors
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='bulk-activate')
    def bulk_activate(self, request):
        """Activates multiple parties specified by IDs."""
        party_ids = request.data.get('ids', [])
        if not isinstance(party_ids, list) or not all(isinstance(i, int) for i in party_ids):
            return Response({'error': _("'ids' must be a list of integer party IDs.")},
                            status=status.HTTP_400_BAD_REQUEST)

        updated_count = Party.objects.filter(pk__in=party_ids).update(is_active=True)
        logger.info(f"Bulk activated {updated_count} parties. IDs: {party_ids}")
        return Response({'message': _('Successfully activated %(count)d parties.') % {'count': updated_count}})

    @action(detail=False, methods=['post'], url_path='bulk-deactivate')
    def bulk_deactivate(self, request):
        """
        Deactivates multiple parties specified by IDs.
        Optional check to prevent deactivating parties with non-zero balances.
        """
        party_ids = request.data.get('ids', [])
        if not isinstance(party_ids, list) or not all(isinstance(i, int) for i in party_ids):
            return Response({'error': _("'ids' must be a list of integer party IDs.")},
                            status=status.HTTP_400_BAD_REQUEST)

        parties_to_check = Party.objects.filter(pk__in=party_ids, is_active=True)
        warnings = []
        allowed_ids_to_deactivate = list(party_ids)  # Start assuming all are allowed

        # Get this flag from settings
        check_balance_before_deactivation = getattr(settings, 'ACCOUNTING_CHECK_BALANCE_BEFORE_DEACTIVATION', True)

        if check_balance_before_deactivation:
            allowed_ids_to_deactivate = []
            for party in parties_to_check:
                try:
                    balance = party.calculate_outstanding_balance()
                    if balance == Decimal('0.00'):
                        allowed_ids_to_deactivate.append(party.pk)
                    else:
                        warning_msg = _(
                            "Party '%(name)s' (ID: %(id)d) was not deactivated due to non-zero balance (%(balance)s).")
                        warnings.append(warning_msg % {'name': party.name, 'id': party.pk, 'balance': balance})
                except Exception as e:
                    logger.error(f"Error checking balance for Party {party.name} (ID: {party.pk}): {e}")
                    continue

        # Deactivate allowed parties
        updated_count = Party.objects.filter(pk__in=allowed_ids_to_deactivate).update(is_active=False)
        logger.info(f"Bulk deactivated {updated_count} parties. IDs: {allowed_ids_to_deactivate}")

        return Response({
            'message': _('Successfully deactivated %(count)d parties.') % {'count': updated_count},
            'warnings': warnings
        })

