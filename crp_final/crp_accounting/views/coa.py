# crp_accounting/views.py

import logging
from decimal import Decimal
from datetime import date

from django.db import models
from django.db.models import Sum, Q, F, Case, When, Value, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django.core.exceptions import ObjectDoesNotExist # Import for specific error handling

from rest_framework import viewsets, permissions, status, filters, serializers, generics # Added generics
from rest_framework.views import APIView # Added APIView
from rest_framework.exceptions import ValidationError, ParseError
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend

# --- Swagger/Spectacular Imports ---
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiParameter, OpenApiTypes, OpenApiResponse, inline_serializer
)

# --- Model Imports ---
from ..models.coa import Account, AccountGroup
from ..models.journal import VoucherLine, TransactionStatus # Added TransactionStatus

# --- Serializer Imports ---
# Assuming serializers are now in a single file or structure is flattened
from ..serializers import (
    AccountGroupReadSerializer,
    AccountGroupWriteSerializer,
    AccountReadSerializer,
    AccountWriteSerializer,
    AccountLedgerEntrySerializer, # Ledger serializer
    AccountLedgerResponseSerializer # Ledger serializer
)
from django.utils.translation import gettext_lazy as _
# --- Service Imports ---
from ..services import ledger_service # Import the ledger service

# --- Enum Imports ---
# from crp_core.enums import DrCrType, AccountNature # No longer needed directly in view

# --- Standard Pagination (Define once) ---
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 1000
logger = logging.getLogger(__name__)
# =============================================================================
# AccountGroup ViewSet (Updated: Removed is_primary)
# =============================================================================
@extend_schema_view(
    list=extend_schema(summary="List Account Groups", description="Retrieve a paginated list of account groups."),
    retrieve=extend_schema(summary="Retrieve Account Group", description="Get details of a specific account group."),
    create=extend_schema(summary="Create Account Group", description="Create a new account group."),
    update=extend_schema(summary="Update Account Group", description="Update an existing account group."),
    partial_update=extend_schema(summary="Partial Update Account Group", description="Update parts of an existing account group."),
    destroy=extend_schema(summary="Delete Account Group", description="Delete an account group (if empty)."),
)
class AccountGroupViewSet(viewsets.ModelViewSet):
    """API endpoint for managing Account Groups (COA Structure)."""
    queryset = AccountGroup.objects.all().select_related('parent_group').prefetch_related('sub_groups', 'accounts').order_by('name') # Ensure consistent order
    permission_classes = [permissions.IsAuthenticated] # TODO: Refine permissions
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['parent_group', 'name'] # Removed is_primary
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'parent_group__name', 'created_at'] # Removed is_primary
    ordering = ['name']

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return AccountGroupReadSerializer
        return AccountGroupWriteSerializer

    def perform_destroy(self, instance):
        """Prevent deletion if group has children (sub-groups or accounts)."""
        if instance.sub_groups.exists():
            raise ValidationError(_("Cannot delete group '%(name)s' because it has sub-groups.") % {'name': instance.name})
        if instance.accounts.exists():
             raise ValidationError(_("Cannot delete group '%(name)s' because it has accounts linked.") % {'name': instance.name})
        try:
            # Let default deletion handle ProtectedError from ForeignKey constraints if any missed
            super().perform_destroy(instance)
        except models.ProtectedError as e:
             # Provide a user-friendly message if DB constraint prevents deletion
             logger.warning(f"ProtectedError deleting AccountGroup {instance.pk}: {e}")
             raise ValidationError(_("Cannot delete this group due to existing relationships (check Vouchers or other links)."))

# =============================================================================
# Account ViewSet (Updated: Uses stored balance, simplified get_queryset)
# =============================================================================
@extend_schema_view(
    list=extend_schema(
        summary="List Accounts",
        description="Retrieve a paginated list of accounts (ledgers), supporting filtering and searching. Includes the last calculated balance.",
        # Removed date_upto parameter as balance is now stored
    ),
    retrieve=extend_schema(
        summary="Retrieve Account",
        description="Get details of a specific account, including its last calculated balance.",
        # Removed date_upto parameter
    ),
    create=extend_schema(summary="Create Account"),
    update=extend_schema(summary="Update Account"),
    partial_update=extend_schema(summary="Partial Update Account"),
    destroy=extend_schema(summary="Delete Account (if no transactions)."),
    balance_as_of=extend_schema( # Kept for dynamic calculation
        summary="Balance As Of Date",
        description="Get the calculated balance for a specific account as of a given date (dynamic calculation).",
        parameters=[
            OpenApiParameter(name='date', description='Target date (YYYY-MM-DD) for balance calculation.', required=True, type=OpenApiTypes.DATE),
        ],
        responses={200: inline_serializer(
            name='BalanceResponse',
            fields={
                'account_id': serializers.IntegerField(),
                'date_as_of': serializers.DateField(),
                'balance': serializers.DecimalField(max_digits=20, decimal_places=2),
            }
        )}
    ),
    # bulk actions schema remain the same
    bulk_activate=extend_schema(summary="Bulk Activate Accounts", request=inline_serializer(name='BulkIDs', fields={'ids': serializers.ListField(child=serializers.IntegerField())}), responses={200: inline_serializer(name='BulkActivateResponse', fields={'message': serializers.CharField()})}),
    bulk_deactivate=extend_schema(summary="Bulk Deactivate Accounts", request=inline_serializer(name='BulkIDs', fields={'ids': serializers.ListField(child=serializers.IntegerField())}), responses={200: inline_serializer(name='BulkDeactivateResponse', fields={'message': serializers.CharField()})}),
)
class AccountViewSet(viewsets.ModelViewSet):
    """API endpoint for managing Accounts (Ledgers) in the Chart of Accounts."""
    # Base queryset, select related group for efficiency
    queryset = Account.objects.select_related('account_group').all()
    permission_classes = [permissions.IsAuthenticated] # TODO: Refine permissions
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # Define specific filter lookups
    filterset_fields = {
        'account_group': ['exact'],
        'account_group__name': ['exact', 'icontains'],
        'account_type': ['exact', 'in'], # Allow filtering by multiple types
        'account_nature': ['exact'],
        'currency': ['exact'],
        'is_active': ['exact'],
        'allow_direct_posting': ['exact'],
        'is_control_account': ['exact'],
        'control_account_party_type': ['exact', 'isnull'],
    }
    search_fields = ['account_number', 'account_name', 'description', 'account_group__name']
    ordering_fields = [
        'account_number', 'account_name', 'account_group__name', 'account_type',
        'is_active', 'created_at', 'current_balance', 'updated_at' # Allow ordering by balance
    ]
    ordering = ['account_group__name', 'account_number'] # Default ordering

    def get_serializer_class(self):
        """Select serializer based on action."""
        if self.action in ['create', 'update', 'partial_update']:
            return AccountWriteSerializer
        # Use read serializer for list/retrieve, which includes 'current_balance'
        return AccountReadSerializer

    def get_queryset(self):
        """
        Basic queryset retrieval. Balance annotation removed as balance
        is now stored on the model and included via AccountReadSerializer.
        """
        # The base queryset is sufficient now. Filtering/Ordering handled by backends.
        return super().get_queryset()

    def perform_destroy(self, instance):
        """Prevent deletion if account has posted transactions."""
        # Check for associated POSTED voucher lines
        if VoucherLine.objects.filter(
            account=instance,
            voucher__status=TransactionStatus.POSTED # Check only posted
            ).exists():
            raise ValidationError(
                _("Cannot delete account '%(name)s' (%(number)s) because it has posted journal entries.") %
                {'name': instance.account_name, 'number': instance.account_number}
            )
        # Allow deletion if only DRAFT entries exist, or no entries exist
        try:
            logger.warning(f"Deleting Account {instance.pk} which has no POSTED entries.")
            super().perform_destroy(instance)
        except models.ProtectedError as e:
             # Catch other potential FK issues (e.g., if linked elsewhere)
             logger.warning(f"ProtectedError deleting Account {instance.pk}: {e}")
             raise ValidationError(_("Deletion failed due to protected relationships."))

    # --- Custom Actions ---
    @extend_schema( # Ensure custom action schema is updated
        summary="Get Balance As Of Date",
        description="Calculates and returns the account balance dynamically as of a specific date.",
        parameters=[OpenApiParameter(name='date', description='Target date (YYYY-MM-DD)', required=True, type=OpenApiTypes.DATE)],
        responses={200: inline_serializer(name='BalanceAsOfResponse', fields={'account_id': serializers.IntegerField(), 'date_as_of': serializers.DateField(), 'balance': serializers.DecimalField(max_digits=20, decimal_places=2)})}
    )
    @action(detail=True, methods=['get'], url_path='balance-as-of')
    def balance_as_of(self, request, pk=None):
        """Calculates and returns the account balance dynamically as of a specific date."""
        account = self.get_object() # Get the specific Account instance
        date_str = request.query_params.get('date', None)
        if not date_str:
            raise ParseError(detail=_("Missing 'date' query parameter (YYYY-MM-DD)."))
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
             raise ParseError(detail=_("Invalid date format for 'date'. Use YYYY-MM-DD."))

        # Call the dynamic balance calculation method on the model instance
        try:
             # Use the dynamic calculation method, which now needs the end date (inclusive)
             balance = account.get_dynamic_balance(date_upto=target_date)
             return Response({'account_id': account.id, 'date_as_of': target_date, 'balance': balance})
        except ValueError as ve: # Catch potential errors from get_dynamic_balance (e.g., bad nature)
             logger.error(f"Error calculating dynamic balance for Account {pk} as of {target_date}: {ve}")
             raise ValidationError(str(ve)) # Raise as DRF validation error
        except Exception as e:
             logger.exception(f"Unexpected error in balance_as_of for Account {pk} as of {target_date}: {e}")
             # Handle other potential errors
             return Response({"detail": _("An unexpected error occurred calculating the balance.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @extend_schema(summary="Bulk Activate Accounts") # Schema simplified, details in decorator
    @action(detail=False, methods=['post'], url_path='bulk-activate')
    def bulk_activate(self, request):
       """Activates multiple accounts based on provided IDs."""
       account_ids = request.data.get('ids', [])
       if not isinstance(account_ids, list) or not all(isinstance(id, int) for id in account_ids):
            return Response({'detail': _("'ids' must be a list of integer account IDs.")}, status=status.HTTP_400_BAD_REQUEST)
       if not account_ids:
           return Response({'detail': _("No account IDs provided.")}, status=status.HTTP_400_BAD_REQUEST)

       updated_count = Account.objects.filter(pk__in=account_ids).update(is_active=True)
       return Response({'message': _("Successfully activated %(count)d accounts.") % {'count': updated_count}})

    @extend_schema(summary="Bulk Deactivate Accounts") # Schema simplified
    @action(detail=False, methods=['post'], url_path='bulk-deactivate')
    def bulk_deactivate(self, request):
        """Deactivates multiple accounts based on provided IDs."""
        account_ids = request.data.get('ids', [])
        if not isinstance(account_ids, list) or not all(isinstance(id, int) for id in account_ids):
            return Response({'detail': _("'ids' must be a list of integer account IDs.")}, status=status.HTTP_400_BAD_REQUEST)
        if not account_ids:
            return Response({'detail': _("No account IDs provided.")}, status=status.HTTP_400_BAD_REQUEST)

        # Add check: Prevent deactivating accounts with current balance? Optional business rule.
        # if Account.objects.filter(pk__in=account_ids, current_balance__ne=Decimal('0.00')).exists():
        #    return Response({'detail': _("Cannot deactivate accounts with non-zero balance.")}, status=status.HTTP_400_BAD_REQUEST)

        updated_count = Account.objects.filter(pk__in=account_ids).update(is_active=False)
        return Response({'message': _("Successfully deactivated %(count)d accounts.") % {'count': updated_count}})


# =============================================================================
# Ledger Views (New Views Added)
# =============================================================================

@extend_schema_view(
    get=extend_schema(
        summary="Get Current Account Balance",
        description="Retrieve the last calculated (stored) balance for a specific account.",
        responses={200: inline_serializer( # Define response structure
            name='CurrentBalanceResponse',
            fields={
                'id': serializers.IntegerField(),
                'account_code': serializers.CharField(),
                'account_name': serializers.CharField(),
                'current_balance': serializers.DecimalField(max_digits=20, decimal_places=2),
                'balance_last_updated': serializers.DateTimeField(allow_null=True),
            }
        )}
    )
)
class AccountBalanceView(APIView):
    """API endpoint to get the current stored balance of a single account."""
    permission_classes = [permissions.IsAuthenticated] # TODO: Refine permissions

    def get(self, request, account_pk, format=None):
        """Returns the current balance stored on the account record."""
        try:
            # Fetch relevant fields efficiently
            account = Account.objects.values(
                'id', 'account_number', 'account_name', 'current_balance', 'balance_last_updated'
            ).get(pk=account_pk)
            return Response(account) # Return fetched dictionary directly
        except Account.DoesNotExist:
            raise Http404(_("Account not found."))


@extend_schema_view(
    get=extend_schema(
        summary="Get Account Ledger",
        description="Retrieve the detailed transaction history (ledger) for an account within a date range.",
        parameters=[
            OpenApiParameter(name='start_date', description='Start date (YYYY-MM-DD) for ledger period (inclusive).', required=False, type=OpenApiTypes.DATE),
            OpenApiParameter(name='end_date', description='End date (YYYY-MM-DD) for ledger period (inclusive).', required=False, type=OpenApiTypes.DATE),
            # Include pagination parameters automatically via pagination_class
        ],
        responses={200: AccountLedgerResponseSerializer} # Reference the response serializer
    )
)
class AccountLedgerView(generics.GenericAPIView): # Use GenericAPIView for pagination
    """
    API endpoint to retrieve the ledger history for a specific account.
    Uses ledger_service for calculations and supports pagination.
    """
    permission_classes = [permissions.IsAuthenticated] # TODO: Refine permissions
    serializer_class = AccountLedgerEntrySerializer # Serializer for the paginated *entries*
    pagination_class = StandardResultsSetPagination # Apply standard pagination

    def get(self, request, account_pk, format=None):
        """Returns the ledger details for the account within the specified date range."""
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        start_date = None
        end_date = None

        try:
            if start_date_str:
                start_date = date.fromisoformat(start_date_str)
            if end_date_str:
                end_date = date.fromisoformat(end_date_str)
        except ValueError:
            raise ParseError(detail=_("Invalid date format. Use YYYY-MM-DD."))

        try:
            # --- Call the Ledger Service ---
            ledger_data = ledger_service.get_account_ledger_data(account_pk, start_date, end_date)
            # --- End Service Call ---

            # --- Paginate the 'entries' list ---
            # paginate_queryset expects an iterable (list, queryset)
            page = self.paginate_queryset(ledger_data['entries'])
            if page is not None:
                # Serialize the entries *within the current page*
                entry_serializer = self.get_serializer(page, many=True)
                # Get the paginated response structure (includes count, next, previous)
                paginated_response = self.get_paginated_response(entry_serializer.data)

                # --- Construct final response ---
                # Create the summary data using the overall response serializer
                # Pass the full ledger_data dict (excluding entries as they are paginated)
                summary_data_to_serialize = {k: v for k, v in ledger_data.items() if k != 'entries'}
                summary_serializer = AccountLedgerResponseSerializer(summary_data_to_serialize)

                # Add the summary data to the paginated response object's data dictionary
                # Use update() to merge the dictionaries
                paginated_response.data.update(summary_serializer.data)
                # Add the paginated entries under the 'entries' key
                paginated_response.data['entries'] = entry_serializer.data # Add entries for the current page

                return paginated_response
            else:
                # If pagination is not active or queryset is empty, return full data (unlikely with pagination class set)
                # Serialize the entire result using the main response serializer
                # Note: This would require AccountLedgerResponseSerializer to have `entries = AccountLedgerEntrySerializer(many=True)`
                full_serializer = AccountLedgerResponseSerializer(ledger_data)
                return Response(full_serializer.data)

        except ObjectDoesNotExist as e: # Catch specific error from service
             raise Http404(str(e)) # Reraise as Http404
        except ValueError as ve: # Catch invalid account nature error from service
            logger.warning(f"Value error generating ledger for account {account_pk}: {ve}")
            return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unexpected error generating ledger for account {account_pk}: {e}")
            return Response({"detail": _("An unexpected server error occurred while generating the ledger.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)