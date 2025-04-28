import logging
from rest_framework import viewsets, status, permissions, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend # For filtering
from rest_framework import filters # For search and ordering
from django.utils.translation import gettext_lazy as _
from crp_core.enums import TransactionStatus, VoucherType
# --- Model Imports ---
from ..models.journal import Voucher

# --- Serializer Imports ---
from ..serializers import VoucherSerializer

# --- Service Function Imports ---
from ..services import voucher_service



# --- Custom Exception Imports ---
from ..exceptions import (
    VoucherWorkflowError, InvalidVoucherStatusError, PeriodLockedError,
    BalanceError, InsufficientPermissionError
)
# --- FilterSet Import ---
from ..filters import VoucherFilterSet

# --- Permission Class Imports ---
from ..permissions import (
    CanViewVoucher, CanManageDraftVoucher, CanSubmitVoucher,
    CanApproveVoucher, CanRejectVoucher, CanReverseVoucher
)

logger = logging.getLogger(__name__)

# =============================================================================
# Voucher ViewSet (Updated)
# =============================================================================

class VoucherViewSet(viewsets.ModelViewSet):
    """API endpoint for managing Vouchers."""
    serializer_class = VoucherSerializer
    # Set default permission - Can only view unless other permissions grant more
    # permission_classes = [CanViewVoucher] # Base permission
    permission_classes = [permissions.IsAuthenticated]

    # --- Filtering, Searching, Ordering ---
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = VoucherFilterSet # Use the defined FilterSet
    search_fields = ['voucher_number', 'narration', 'reference', 'lines__narration', 'party__name']
    ordering_fields = ['date', 'voucher_number', 'status', 'updated_at', 'created_at']
    ordering = ['-date', '-voucher_number']

    def get_queryset(self):
        """Optimize queryset fetching."""
        # Base queryset (permissions applied later by DRF)
        queryset = Voucher.objects.all().select_related(
            'accounting_period', 'party'
        ).prefetch_related(
            'lines__account', 'approvals__user'
        )
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def get_permissions(self):
        """
        Instantiate and return the list of permissions that this view requires,
        potentially varying by action.
        """
        if self.action == 'list':
            # Anyone authenticated can list (if CanViewVoucher allows)
            # permission_classes = [CanViewVoucher]
            permission_classes = [permissions.IsAuthenticated]
        elif self.action == 'create':

            # permission_classes = [CanManageDraftVoucher] # Checks 'add_voucher'
            permission_classes = [permissions.IsAuthenticated]
        elif self.action in ['update', 'partial_update', 'destroy', 'retrieve']:
            # Requires object-level checks handled within CanManageDraftVoucher
            # permission_classes = [CanManageDraftVoucher]
            permission_classes = [permissions.IsAuthenticated]
        # Custom actions will have permissions set via decorator or implicitly inherit
        else:
             # permission_classes = self.permission_classes # Default defined on class
             permission_classes = [permissions.IsAuthenticated]

        return [permission() for permission in permission_classes]

    # --- Overriding Standard Methods (No change needed here, handled by permissions) ---
    def perform_create(self, serializer):
        try:
            # serializer.save(created_by=self.request.user) # If you have created_by
            serializer.save()
            logger.info(f"Voucher created by User {self.request.user.username}")
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else str(e))

    def perform_update(self, serializer):
        try:
            # serializer.save(updated_by=self.request.user) # If you have updated_by
            serializer.save()
            logger.info(f"Voucher {serializer.instance.pk} updated by User {self.request.user.username}")
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else str(e))

    def perform_destroy(self, instance):
        # Permission check happens via get_permissions() and CanManageDraftVoucher
        logger.warning(f"Attempting deletion: Voucher {instance.pk} by User {self.request.user.username}")
        instance.delete() # Permission class already validated state/permission
        logger.info(f"Deletion successful: Voucher {instance.pk} by User {self.request.user.username}")


    # --- Custom Workflow Actions (Apply specific permissions) ---

    @action(detail=True, methods=['post'], url_path='submit',permission_classes = [permissions.IsAuthenticated])
            # permission_classes=[CanSubmitVoucher]) # Specific permission
    def submit(self, request, pk=None):
        """Submits a DRAFT voucher for approval."""
        try:
            voucher = self.get_object()
            updated_voucher = voucher_service.submit_voucher_for_approval(
                voucher_id=voucher.pk, submitted_by_user=request.user
            )
            serializer = self.get_serializer(updated_voucher)
            return Response(serializer.data, status=status.HTTP_200_OK)
        # --- Error Handling (keep as before, InsufficientPermissionError caught automatically by DRF) ---
        except (Http404):
            return Response({"detail": _("Voucher not found.")}, status=status.HTTP_404_NOT_FOUND)
        except (InvalidVoucherStatusError, PeriodLockedError, BalanceError, VoucherWorkflowError, DjangoValidationError) as e:
            error_detail = e.message_dict if hasattr(e, 'message_dict') else str(e)
            return Response({"detail": error_detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unexpected error submitting Voucher {pk}: {e}")
            return Response({"detail": _("An unexpected server error occurred.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='approve',
            permission_classes=[CanApproveVoucher]) # Specific permission
    def approve(self, request, pk=None):
        """Approves a voucher and posts it."""
        comments = request.data.get('comments', "")
        try:
            voucher = self.get_object()
            posted_voucher = voucher_service.approve_and_post_voucher(
                voucher_id=voucher.pk, approver_user=request.user, comments=comments
            )
            serializer = self.get_serializer(posted_voucher)
            return Response(serializer.data, status=status.HTTP_200_OK)
        # --- Error Handling ---
        except (Http404):
            return Response({"detail": _("Voucher not found.")}, status=status.HTTP_404_NOT_FOUND)
        except (InvalidVoucherStatusError, PeriodLockedError, BalanceError, VoucherWorkflowError, DjangoValidationError) as e:
            error_detail = e.message_dict if hasattr(e, 'message_dict') else str(e)
            return Response({"detail": error_detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unexpected error approving Voucher {pk}: {e}")
            return Response({"detail": _("An unexpected server error occurred.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='reject',
            permission_classes=[CanRejectVoucher]) # Specific permission
    def reject(self, request, pk=None):
        """Rejects a PENDING_APPROVAL voucher."""
        comments = request.data.get('comments')
        if not comments or not comments.strip():
            return Response({"comments": [_("Rejection comments are mandatory.")]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            voucher = self.get_object()
            rejected_voucher = voucher_service.reject_voucher(
                voucher_id=voucher.pk, rejecting_user=request.user, comments=comments
            )
            serializer = self.get_serializer(rejected_voucher)
            return Response(serializer.data, status=status.HTTP_200_OK)
        # --- Error Handling ---
        except (Http404):
            return Response({"detail": _("Voucher not found.")}, status=status.HTTP_404_NOT_FOUND)
        except (InvalidVoucherStatusError, VoucherWorkflowError, DjangoValidationError) as e:
            error_detail = e.message_dict if hasattr(e, 'message_dict') else str(e)
            return Response({"detail": error_detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unexpected error rejecting Voucher {pk}: {e}")
            return Response({"detail": _("An unexpected server error occurred.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['post'], url_path='reverse',
            permission_classes=[CanReverseVoucher]) # Specific permission
    def reverse(self, request, pk=None):
        """Creates a reversing entry for a POSTED voucher."""
        reversal_date_str = request.data.get('reversal_date')
        reversal_voucher_type = request.data.get('reversal_voucher_type', VoucherType.GENERAL)
        post_immediately = request.data.get('post_immediately', False)
        reversal_date = None
        if reversal_date_str:
            try:
                from datetime import date
                reversal_date = date.fromisoformat(reversal_date_str)
            except ValueError:
                 return Response({"reversal_date": [_("Invalid date format. Use YYYY-MM-DD.")]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            original_voucher = self.get_object()
            reversing_voucher = voucher_service.create_reversing_voucher(
                original_voucher_id=original_voucher.pk, user=request.user,
                reversal_date=reversal_date, reversal_voucher_type=reversal_voucher_type,
                post_immediately=post_immediately
            )
            serializer = self.get_serializer(reversing_voucher)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        # --- Error Handling ---
        except (Http404):
             return Response({"detail": _("Original voucher to reverse not found.")}, status=status.HTTP_404_NOT_FOUND)
        except (InvalidVoucherStatusError, PeriodLockedError, BalanceError, VoucherWorkflowError, DjangoValidationError) as e:
             error_detail = e.message_dict if hasattr(e, 'message_dict') else str(e)
             return Response({"detail": error_detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
             logger.exception(f"Unexpected error creating reversal for Voucher {pk}: {e}")
             return Response({"detail": _("An unexpected server error occurred.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
# from rest_framework import viewsets, status
# from rest_framework.response import Response
# from rest_framework.permissions import IsAuthenticated
# from django.db import transaction
# from django.utils.translation import gettext_lazy as _
# 
# from crp_accounting.models.journal import JournalEntry
# from crp_accounting.models.period import AccountingPeriod
# from crp_accounting.serializers.journal import JournalEntrySerializer
# from crp_core.enums import DrCrType
# 
# 
# class JournalEntryViewSet(viewsets.ModelViewSet):
#     """
#     ViewSet to manage journal entries.
#     Implements create, list, retrieve, update, and delete with full accounting principles.
#     """
#     queryset = JournalEntry.objects.prefetch_related('lines').select_related('accounting_period').all()
#     serializer_class = JournalEntrySerializer
#     permission_classes = [IsAuthenticated]
# 
#     def create(self, request, *args, **kwargs):
#         """
#         Custom create logic with double-entry validation and real-time balance update.
#         Includes check for locked accounting periods.
#         """
#         serializer = self.get_serializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
# 
#         accounting_period = serializer.validated_data.get('accounting_period')
#         if accounting_period and accounting_period.locked:
#             return Response(
#                 {'error': _("Cannot create entry. The selected accounting period is locked.")},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
# 
#         try:
#             with transaction.atomic():
#                 journal_entry = serializer.save()
#                 return Response(
#                     self.get_serializer(journal_entry).data,
#                     status=status.HTTP_201_CREATED
#                 )
#         except Exception as e:
#             return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
# 
#     def update(self, request, *args, **kwargs):
#         """
#         Full update with account balance recalculation.
#         Includes lock check to prevent modification in locked periods.
#         """
#         partial = kwargs.pop('partial', False)
#         instance = self.get_object()
# 
#         serializer = self.get_serializer(instance, data=request.data, partial=partial)
#         serializer.is_valid(raise_exception=True)
# 
#         # Check current or new accounting period is locked
#         new_period = serializer.validated_data.get('accounting_period') or instance.accounting_period
#         if new_period and new_period.locked:
#             return Response(
#                 {'error': _("This accounting period is locked. Cannot update journal entry.")},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
# 
#         try:
#             with transaction.atomic():
#                 updated_instance = serializer.save()
#                 return Response(
#                     self.get_serializer(updated_instance).data,
#                     status=status.HTTP_200_OK
#                 )
#         except Exception as e:
#             return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
# 
#     def destroy(self, request, *args, **kwargs):
#         """
#         Deletes journal entry and rolls back balances.
#         Prevent deletion if the accounting period is locked.
#         """
#         instance = self.get_object()
# 
#         if instance.accounting_period and instance.accounting_period.locked:
#             return Response(
#                 {'error': _("This accounting period is locked. Cannot delete journal entry.")},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
# 
#         try:
#             with transaction.atomic():
#                 for line in instance.lines.all():
#                     account = line.account
#                     if line.dr_cr == DrCrType.DEBIT.name:
#                         account.balance -= line.amount
#                     else:
#                         account.balance += line.amount
#                     account.save()
# 
#                 instance.delete()
#                 return Response(status=status.HTTP_204_NO_CONTENT)
# 
#         except Exception as e:
#             return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
