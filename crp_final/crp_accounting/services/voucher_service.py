# crp_accounting/services/voucher_service.py

import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from typing import Optional

# --- Model Imports ---
from ..models.journal import (
    Voucher, VoucherLine, VoucherApproval, VoucherSequence,
    VoucherType, TransactionStatus, DrCrType, ApprovalActionType
)
from ..models.coa import Account
from ..models.party import Party
from ..models.period import AccountingPeriod
# --- Service Imports ---
from .voucher_utils import assign_voucher_number
# --- Task/Signal Imports ---
from ..tasks import update_account_balances_task
# --- Custom Exception Imports ---
from ..exceptions import (
    VoucherWorkflowError, InvalidVoucherStatusError, PeriodLockedError,
    BalanceError, InsufficientPermissionError
)

logger = logging.getLogger(__name__)

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# !! WARNING: PERMISSIONS ARE TEMPORARILY BYPASSED IN THIS FILE FOR TESTING !!
# !! TODO: Reinstate proper RBAC permission checks before production.       !!
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


# --- Permission Checking Helper (Temporarily Bypassed) ---
def _check_permission(user: settings.AUTH_USER_MODEL, permission_codename: str, voucher: Optional[Voucher] = None):
    """
    Checks if the user has the required permission, potentially for a specific voucher object.
    Raises InsufficientPermissionError if the check fails.
    Leverages Django's built-in permission framework.

    *** TEMPORARILY DISABLED FOR TESTING ***
    """
    permission_string = f'{Voucher._meta.app_label}.{permission_codename}'
    user_identifier = user.get_username()
    logger.debug(f"[BYPASSED] Permission Check Skipped: '{permission_string}' for user '{user_identifier}' on object {voucher.pk if voucher else 'N/A'}")
    # --- Actual Check Commented Out ---
    # if not user.has_perm(permission_string, voucher):
    #     logger.warning(f"Permission denied: User '{user_identifier}' lacks '{permission_string}' for Voucher {voucher.pk if voucher else 'N/A'}")
    #     raise InsufficientPermissionError(f"Permission '{permission_string}' required.")
    # logger.debug(f"Permission Granted: User '{user_identifier}' has '{permission_string}'")
    pass # Allow execution to continue


# --- Core Workflow Service Functions ---

@transaction.atomic
def submit_voucher_for_approval(voucher_id: int, submitted_by_user: settings.AUTH_USER_MODEL) -> Voucher:
    """
    Submits a DRAFT voucher for the approval workflow.
    """
    user_identifier = submitted_by_user.get_username()
    logger.info(f"Attempting submission: Voucher PK {voucher_id} by User '{user_identifier}'")
    # TODO: Reinstate proper RBAC permission checks.
    _check_permission(submitted_by_user, 'submit_voucher') # Call is kept, but function body is bypassed

    voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)

    if voucher.status != TransactionStatus.DRAFT.name:
        raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=[TransactionStatus.DRAFT])

    _validate_voucher_essentials(voucher)

    if not voucher.voucher_number:
        logger.info(f"Voucher {voucher.pk} requires number assignment.")
        try:
            assign_voucher_number(voucher)
        except DjangoValidationError as e:
             logger.error(f"Voucher numbering failed during submission for Voucher {voucher.pk}: {e}", exc_info=True)
             raise VoucherWorkflowError(_("Failed to assign voucher number during submission.")) from e

    original_status = voucher.status
    voucher.status = TransactionStatus.PENDING_APPROVAL.name
    voucher.updated_at = timezone.now()
    voucher.save(update_fields=['status', 'voucher_number', 'updated_at'])

    _log_approval_action(
        voucher=voucher, user=submitted_by_user, action_type=ApprovalActionType.SUBMITTED.name,
        from_status=original_status, to_status=voucher.status, comments=_("Submitted for approval.")
    )
    logger.info(f"Submission successful: Voucher {voucher.voucher_number} by User '{user_identifier}'")
    return voucher


@transaction.atomic
def approve_and_post_voucher(voucher_id: int, approver_user: settings.AUTH_USER_MODEL, comments: str = "") -> Voucher:
    """
    Approves a voucher (Pending or Rejected) and immediately posts it.
    """
    user_identifier = approver_user.get_username()
    logger.info(f"Attempting approval & posting: Voucher PK {voucher_id} by User '{user_identifier}'")
    # TODO: Reinstate proper RBAC permission checks.
    _check_permission(approver_user, 'approve_voucher') # Call is kept, but function body is bypassed

    voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)
    voucher_display_id = voucher.voucher_number or f"PK {voucher.pk}"

    allowed_statuses = [TransactionStatus.PENDING_APPROVAL.name, TransactionStatus.REJECTED.name]
    if voucher.status not in allowed_statuses:
        raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=allowed_statuses)

    _validate_voucher_for_posting(voucher)

    original_status = voucher.status
    voucher.status = TransactionStatus.POSTED.name
    voucher.updated_at = timezone.now()
    voucher.save(update_fields=['status', 'updated_at'])

    _log_approval_action(
        voucher=voucher, user=approver_user, action_type=ApprovalActionType.APPROVED.name,
        from_status=original_status, to_status=voucher.status, comments=comments or _("Approved and Posted.")
    )

    _trigger_balance_updates(voucher)

    logger.info(f"Approval & Posting successful: Voucher {voucher_display_id} by User '{user_identifier}'")
    return voucher


@transaction.atomic
def reject_voucher(voucher_id: int, rejecting_user: settings.AUTH_USER_MODEL, comments: str) -> Voucher:
    """
    Rejects a voucher currently PENDING_APPROVAL.
    """
    user_identifier = rejecting_user.get_username()
    logger.info(f"Attempting rejection: Voucher PK {voucher_id} by User '{user_identifier}'")
    # TODO: Reinstate proper RBAC permission checks.
    _check_permission(rejecting_user, 'reject_voucher') # Call is kept, but function body is bypassed

    voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)
    voucher_display_id = voucher.voucher_number or f"PK {voucher.pk}"

    if voucher.status != TransactionStatus.PENDING_APPROVAL.name:
         raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=[TransactionStatus.PENDING_APPROVAL])

    if not comments or not comments.strip():
        raise DjangoValidationError({'comments': _("Rejection comments are mandatory.")})

    original_status = voucher.status
    voucher.status = TransactionStatus.REJECTED.name
    voucher.updated_at = timezone.now()
    voucher.save(update_fields=['status', 'updated_at'])

    _log_approval_action(
        voucher=voucher, user=rejecting_user, action_type=ApprovalActionType.REJECTED.name,
        from_status=original_status, to_status=voucher.status, comments=comments
    )
    logger.warning(f"Rejection successful: Voucher {voucher_display_id} by User '{user_identifier}'. Reason: {comments}")
    return voucher


@transaction.atomic
def create_reversing_voucher(
    original_voucher_id: int,
    user: settings.AUTH_USER_MODEL,
    reversal_date: Optional[timezone.datetime.date] = None,
    reversal_voucher_type: str = VoucherType.GENERAL.name,
    post_immediately: bool = False
) -> Voucher:
    """
    Creates a reversing voucher for a previously POSTED voucher.
    """
    user_identifier = user.get_username()
    logger.info(
        f"Attempting reversal creation for Original Voucher PK {original_voucher_id} by User '{user_identifier}' "
        f"(Post Immediately: {post_immediately}, Type: {reversal_voucher_type})"
    )
    # TODO: Reinstate proper RBAC permission checks.
    _check_permission(user, 'create_reversal_voucher') # Call is kept, but function body is bypassed

    try:
        original_voucher = Voucher.objects.prefetch_related('lines__account').get(pk=original_voucher_id)
    except Voucher.DoesNotExist:
        logger.error(f"Original voucher PK {original_voucher_id} not found for reversal.")
        raise

    if original_voucher.status != TransactionStatus.POSTED.name:
         raise InvalidVoucherStatusError(
             current_status=original_voucher.status, expected_statuses=[TransactionStatus.POSTED],
             message=_("Only 'Posted' vouchers can be reversed.")
         )

    effective_reversal_date = reversal_date or timezone.now().date()
    try:
        reversal_period = AccountingPeriod.objects.get(
            start_date__lte=effective_reversal_date,
            end_date__gte=effective_reversal_date
        )
        if reversal_period.locked:
             raise PeriodLockedError(period_name=str(reversal_period))
    except AccountingPeriod.DoesNotExist:
        raise DjangoValidationError(
            {'reversal_date': _("No open accounting period found for reversal date %(date)s.") % {'date': effective_reversal_date}}
        )

    reversing_voucher = Voucher(
        date=effective_reversal_date,
        effective_date=effective_reversal_date,
        narration=_(f"Reversal of Voucher: {original_voucher.voucher_number}. Original: {original_voucher.narration or ''}")[:255],
        voucher_type=reversal_voucher_type,
        status=TransactionStatus.DRAFT.name,
        party=original_voucher.party,
        accounting_period=reversal_period,
        reference=f"Reversal of {original_voucher.voucher_number}"[:100],
    )
    reversing_voucher.save()

    new_lines = []
    for line in original_voucher.lines.all():
        if not line.account or not line.account.is_active or not line.account.allow_direct_posting:
             logger.error(f"Skipping reversal line for inactive/non-postable account {line.account} from original voucher {original_voucher_id}")
             raise DjangoValidationError(f"Cannot reverse line using inactive/non-postable account: {line.account}")

        new_lines.append(VoucherLine(
            voucher=reversing_voucher,
            account=line.account,
            dr_cr=(DrCrType.CREDIT.name if line.dr_cr == DrCrType.DEBIT.name else DrCrType.DEBIT.name),
            amount=line.amount,
            narration=f"Reversal - {line.narration or ''}"[:255]
        ))

    if not new_lines:
         logger.warning(f"Original voucher {original_voucher_id} has no valid lines to reverse.")
         raise VoucherWorkflowError(_("Original voucher has no lines or uses inactive accounts, cannot reverse."))
    else:
        VoucherLine.objects.bulk_create(new_lines)

    reversing_voucher.refresh_from_db()

    final_status = TransactionStatus.DRAFT.name
    log_action = ApprovalActionType.COMMENTED.name
    log_comment = f"Reversing voucher created in Draft for Original {original_voucher.voucher_number}."

    if post_immediately:
        # TODO: Reinstate proper RBAC permission checks.
        _check_permission(user, 'post_voucher') # Call is kept, but function body is bypassed

        try:
            assign_voucher_number(reversing_voucher)
        except DjangoValidationError as e:
             logger.error(f"Voucher numbering failed during reversal posting for Original Voucher PK {original_voucher.pk}: {e}", exc_info=True)
             raise VoucherWorkflowError(_("Failed to assign voucher number during reversal posting.")) from e

        _validate_voucher_for_posting(reversing_voucher)

        final_status = TransactionStatus.POSTED.name
        reversing_voucher.status = final_status
        reversing_voucher.updated_at = timezone.now()
        reversing_voucher.save(update_fields=['status', 'voucher_number', 'updated_at'])

        _trigger_balance_updates(reversing_voucher)

        log_action = ApprovalActionType.APPROVED.name
        log_comment = f"Automatic posting of reversal for {original_voucher.voucher_number}."
        logger.info(f"Posting successful: Reversing Voucher {reversing_voucher.voucher_number} for Original {original_voucher.voucher_number}")
    else:
        logger.info(f"Reversal Created: Voucher {reversing_voucher.pk} in Draft for Original {original_voucher.voucher_number}")

    _log_approval_action(
        voucher=reversing_voucher, user=user, action_type=log_action,
        from_status=TransactionStatus.DRAFT.name,
        to_status=final_status,
        comments=log_comment
    )

    return reversing_voucher


# --- Internal Helper/Validation Functions (No changes needed below for permissions) ---

def _validate_voucher_essentials(voucher: Voucher):
    """Basic checks before submitting/posting. Raises custom exceptions or DjangoValidationError."""
    if not voucher.is_balanced:
        raise BalanceError()

    period = voucher.accounting_period
    if hasattr(period, 'locked') and period.locked:
        raise PeriodLockedError(period_name=str(period))
    elif not hasattr(period, 'locked'):
         period = AccountingPeriod.objects.get(pk=voucher.accounting_period_id)
         if period.locked:
             raise PeriodLockedError(period_name=str(period))

    if not (period.start_date <= voucher.date <= period.end_date):
        raise DjangoValidationError(
            {'date': _("Voucher date %(v_date)s is outside the accounting period '%(p_name)s' (%(p_start)s to %(p_end)s).") %
             {'v_date': voucher.date, 'p_name': str(period), 'p_start': period.start_date, 'p_end': period.end_date}}
        )

    if not voucher.lines.exists():
         raise DjangoValidationError({'lines': _("Voucher must have at least one line item.")})
    if voucher.lines.filter(account__isnull=True).exists():
        raise DjangoValidationError({'lines': _("One or more voucher lines is missing an account.")})
    if voucher.lines.filter(account__allow_direct_posting=False).exists():
        raise DjangoValidationError({'lines': _("One or more lines uses an account that does not allow direct posting.")})


def _validate_voucher_for_posting(voucher: Voucher):
    """Comprehensive validation specifically before changing status to POSTED."""
    _validate_voucher_essentials(voucher)

    if not voucher.voucher_number:
         logger.error(f"Attempting to post Voucher PK {voucher.pk} without a voucher number.")
         raise VoucherWorkflowError(_("Voucher number is missing. Cannot post."))

    logger.debug(f"Pre-Posting Validation Passed: Voucher {voucher.voucher_number}")


def _trigger_balance_updates(voucher: Voucher):
    """Dispatches the task to update ledger balances."""
    if voucher.status != TransactionStatus.POSTED.name:
        logger.warning(f"Attempted to trigger balance update for non-posted Voucher {voucher.pk} (Status: {voucher.status}). Skipping.")
        return

    logger.info(f"Triggering balance updates for POSTED Voucher {voucher.voucher_number or voucher.pk}")
    try:
        update_account_balances_task.delay(voucher_id=voucher.pk)
        logger.debug(f"Enqueued balance update task for Voucher PK: {voucher.pk}")
    except Exception as e:
        logger.critical(
            f"ALERT: Failed to enqueue balance update task for Voucher {voucher.pk} ({voucher.voucher_number}). "
            f"Balance inconsistency likely. Error: {e}", exc_info=True
        )


def _log_approval_action(voucher: Voucher, user: settings.AUTH_USER_MODEL, action_type: str, from_status: str, to_status: str, comments: str):
     """Helper to create VoucherApproval log entries."""
     try:
         VoucherApproval.objects.create(
             voucher=voucher,
             user=user,
             action_type=action_type,
             from_status=from_status,
             to_status=to_status,
             comments=comments
         )
         logger.debug(f"Logged action '{action_type}' for Voucher {voucher.pk} by user '{user.get_username()}'")
     except Exception as e:
         logger.error(f"Failed to log approval action {action_type} for Voucher {voucher.pk}: {e}", exc_info=True)
# # crp_accounting/services/voucher_service.py
#
# import logging
# from decimal import Decimal
# from django.db import transaction
# from django.utils import timezone
# from django.utils.translation import gettext_lazy as _
# from django.core.exceptions import ValidationError as DjangoValidationError
# from django.conf import settings
# from typing import Optional
#
# # --- Model Imports ---
# # Ensure these paths match your project structure
# from ..models.journal import (
#     Voucher, VoucherLine, VoucherApproval, VoucherSequence,
#     VoucherType, TransactionStatus, DrCrType, ApprovalActionType
# )
# from ..models.coa import Account
# from ..models.party import Party # Assuming Party model is here
# from ..models.period import AccountingPeriod
# # --- Service Imports ---
# from .voucher_utils import assign_voucher_number
# # --- Task/Signal Imports ---
# from ..tasks import update_account_balances_task
# # --- Custom Exception Imports ---
# from ..exceptions import (
#     VoucherWorkflowError, InvalidVoucherStatusError, PeriodLockedError,
#     BalanceError, InsufficientPermissionError
# )
#
# logger = logging.getLogger(__name__)
#
#
# # --- Permission Checking Helper ---
# def _check_permission(user: settings.AUTH_USER_MODEL, permission_codename: str, voucher: Optional[Voucher] = None):
#     """
#     Checks if the user has the required permission, potentially for a specific voucher object.
#     Raises InsufficientPermissionError if the check fails.
#     Leverages Django's built-in permission framework.
#
#     Args:
#         user: The user instance performing the action.
#         permission_codename: The permission codename (e.g., 'submit_voucher') WITHOUT the app label.
#         voucher: Optional voucher instance for object-level permission checks.
#     """
#     # Construct the full permission string (app_label.codename)
#     permission_string = f'{Voucher._meta.app_label}.{permission_codename}' # Use app_label from model for safety
#     user_identifier = user.get_username() # Uses USERNAME_FIELD (email in your case)
#
#     logger.debug(f"Checking permission '{permission_string}' for user '{user_identifier}' on object {voucher.pk if voucher else 'N/A'}")
#
#     # Use Django's built-in permission check
#     if not user.has_perm(permission_string, voucher): # Pass voucher for object-level checks
#         logger.warning(f"Permission denied: User '{user_identifier}' lacks '{permission_string}' for Voucher {voucher.pk if voucher else 'N/A'}")
#         raise InsufficientPermissionError(f"Permission '{permission_string}' required.")
#
#     logger.debug(f"Permission Granted: User '{user_identifier}' has '{permission_string}'")
#
#
# # --- Core Workflow Service Functions ---
#
# @transaction.atomic
# def submit_voucher_for_approval(voucher_id: int, submitted_by_user: settings.AUTH_USER_MODEL) -> Voucher:
#     """
#     Submits a DRAFT voucher for the approval workflow.
#
#     Args:
#         voucher_id: The PK of the voucher to submit.
#         submitted_by_user: The user performing the submission.
#
#     Returns:
#         The updated Voucher instance.
#
#     Raises:
#         Voucher.DoesNotExist, InvalidVoucherStatusError, PeriodLockedError,
#         BalanceError, VoucherWorkflowError, InsufficientPermissionError,
#         DjangoValidationError
#     """
#     user_identifier = submitted_by_user.get_username()
#     logger.info(f"Attempting submission: Voucher PK {voucher_id} by User '{user_identifier}'")
#     _check_permission(submitted_by_user, 'submit_voucher') # Check permission first
#
#     # Lock voucher row
#     voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)
#
#     # 1. Validate current status
#     if voucher.status != TransactionStatus.DRAFT.name:
#         raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=[TransactionStatus.DRAFT])
#
#     # 2. Validate essential data and accounting rules
#     _validate_voucher_essentials(voucher)
#
#     # 3. Assign Voucher Number if needed
#     if not voucher.voucher_number:
#         logger.info(f"Voucher {voucher.pk} requires number assignment.")
#         try:
#             assign_voucher_number(voucher) # Modifies voucher instance in memory
#         except DjangoValidationError as e:
#              logger.error(f"Voucher numbering failed during submission for Voucher {voucher.pk}: {e}", exc_info=True)
#              raise VoucherWorkflowError(_("Failed to assign voucher number during submission.")) from e
#
#     # 4. Update Status & Log
#     original_status = voucher.status
#     voucher.status = TransactionStatus.PENDING_APPROVAL.name
#     voucher.updated_at = timezone.now()
#     # Save status change and potentially the newly assigned voucher number
#     voucher.save(update_fields=['status', 'voucher_number', 'updated_at'])
#
#     _log_approval_action(
#         voucher=voucher, user=submitted_by_user, action_type=ApprovalActionType.SUBMITTED.name,
#         from_status=original_status, to_status=voucher.status, comments=_("Submitted for approval.")
#     )
#     logger.info(f"Submission successful: Voucher {voucher.voucher_number} by User '{user_identifier}'")
#     return voucher
#
#
# @transaction.atomic
# def approve_and_post_voucher(voucher_id: int, approver_user: settings.AUTH_USER_MODEL, comments: str = "") -> Voucher:
#     """
#     Approves a voucher (Pending or Rejected) and immediately posts it.
#
#     Args:
#         voucher_id: The PK of the voucher to approve.
#         approver_user: The user performing the approval.
#         comments: Optional comments for the approval log.
#
#     Returns:
#         The updated Voucher instance.
#
#     Raises:
#         Voucher.DoesNotExist, InvalidVoucherStatusError, PeriodLockedError,
#         BalanceError, VoucherWorkflowError, InsufficientPermissionError,
#         DjangoValidationError
#     """
#     user_identifier = approver_user.get_username()
#     logger.info(f"Attempting approval & posting: Voucher PK {voucher_id} by User '{user_identifier}'")
#     _check_permission(approver_user, 'approve_voucher') # Check permission
#
#     # Lock voucher row
#     voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)
#     voucher_display_id = voucher.voucher_number or f"PK {voucher.pk}" # Use number if available
#
#     # 1. Validate current status
#     allowed_statuses = [TransactionStatus.PENDING_APPROVAL.name, TransactionStatus.REJECTED.name]
#     if voucher.status not in allowed_statuses:
#         raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=allowed_statuses)
#
#     # 2. Perform FINAL validation before posting
#     _validate_voucher_for_posting(voucher) # Includes essentials + posting specific
#
#     # 3. Update Status, Log Approval, and Post
#     original_status = voucher.status
#     voucher.status = TransactionStatus.POSTED.name
#     voucher.updated_at = timezone.now()
#     # Consider adding approved_by/approved_at fields to Voucher model
#     voucher.save(update_fields=['status', 'updated_at'])
#
#     _log_approval_action(
#         voucher=voucher, user=approver_user, action_type=ApprovalActionType.APPROVED.name,
#         from_status=original_status, to_status=voucher.status, comments=comments or _("Approved and Posted.")
#     )
#
#     # 4. Trigger Ledger Balance Updates (Asynchronously)
#     _trigger_balance_updates(voucher)
#
#     logger.info(f"Approval & Posting successful: Voucher {voucher_display_id} by User '{user_identifier}'")
#     return voucher
#
#
# @transaction.atomic
# def reject_voucher(voucher_id: int, rejecting_user: settings.AUTH_USER_MODEL, comments: str) -> Voucher:
#     """
#     Rejects a voucher currently PENDING_APPROVAL.
#
#     Args:
#         voucher_id: The PK of the voucher to reject.
#         rejecting_user: The user performing the rejection.
#         comments: Mandatory comments explaining the rejection.
#
#     Returns:
#         The updated Voucher instance.
#
#     Raises:
#         Voucher.DoesNotExist, InvalidVoucherStatusError, InsufficientPermissionError,
#         DjangoValidationError
#     """
#     user_identifier = rejecting_user.get_username()
#     logger.info(f"Attempting rejection: Voucher PK {voucher_id} by User '{user_identifier}'")
#     _check_permission(rejecting_user, 'reject_voucher') # Check permission
#
#     # Lock voucher row
#     voucher = Voucher.objects.select_for_update().select_related('accounting_period').get(pk=voucher_id)
#     voucher_display_id = voucher.voucher_number or f"PK {voucher.pk}"
#
#     # 1. Validate current status
#     if voucher.status != TransactionStatus.PENDING_APPROVAL.name:
#          raise InvalidVoucherStatusError(current_status=voucher.status, expected_statuses=[TransactionStatus.PENDING_APPROVAL])
#
#     # 2. Enforce rejection comments
#     if not comments or not comments.strip():
#         raise DjangoValidationError({'comments': _("Rejection comments are mandatory.")})
#
#     # 3. Update Status & Log
#     original_status = voucher.status
#     voucher.status = TransactionStatus.REJECTED.name
#     voucher.updated_at = timezone.now()
#     voucher.save(update_fields=['status', 'updated_at'])
#
#     _log_approval_action(
#         voucher=voucher, user=rejecting_user, action_type=ApprovalActionType.REJECTED.name,
#         from_status=original_status, to_status=voucher.status, comments=comments
#     )
#     logger.warning(f"Rejection successful: Voucher {voucher_display_id} by User '{user_identifier}'. Reason: {comments}")
#     return voucher
#
#
# @transaction.atomic
# def create_reversing_voucher(
#     original_voucher_id: int,
#     user: settings.AUTH_USER_MODEL,
#     reversal_date: Optional[timezone.datetime.date] = None,
#     reversal_voucher_type: str = VoucherType.GENERAL.name, # Allow specifying type
#     post_immediately: bool = False
# ) -> Voucher:
#     """
#     Creates a reversing voucher for a previously POSTED voucher.
#
#     Args:
#         original_voucher_id: The PK of the voucher to reverse.
#         user: The user creating the reversal.
#         reversal_date: Optional date for the reversal (defaults to today).
#         reversal_voucher_type: Optional type for the new reversal voucher.
#         post_immediately: If True, attempts to post the reversal directly.
#
#     Returns:
#         The newly created reversing Voucher instance (either Draft or Posted).
#
#     Raises:
#         Voucher.DoesNotExist, InvalidVoucherStatusError, PeriodLockedError,
#         InsufficientPermissionError, VoucherWorkflowError, DjangoValidationError
#     """
#     user_identifier = user.get_username()
#     logger.info(
#         f"Attempting reversal creation for Original Voucher PK {original_voucher_id} by User '{user_identifier}' "
#         f"(Post Immediately: {post_immediately}, Type: {reversal_voucher_type})"
#     )
#     _check_permission(user, 'create_reversal_voucher') # Permission check
#
#     # Fetch original voucher with related lines/accounts
#     try:
#         original_voucher = Voucher.objects.prefetch_related('lines__account').get(pk=original_voucher_id)
#     except Voucher.DoesNotExist:
#         logger.error(f"Original voucher PK {original_voucher_id} not found for reversal.")
#         raise # Re-raise for view to handle as 404
#
#     # 1. Validate Original Voucher Status
#     if original_voucher.status != TransactionStatus.POSTED.name:
#          raise InvalidVoucherStatusError(
#              current_status=original_voucher.status, expected_statuses=[TransactionStatus.POSTED],
#              message=_("Only 'Posted' vouchers can be reversed.")
#          )
#
#     # 2. Determine Reversal Date & Validate Period
#     effective_reversal_date = reversal_date or timezone.now().date()
#     try:
#         reversal_period = AccountingPeriod.objects.get(
#             start_date__lte=effective_reversal_date,
#             end_date__gte=effective_reversal_date
#         )
#         if reversal_period.locked:
#              raise PeriodLockedError(period_name=str(reversal_period))
#     except AccountingPeriod.DoesNotExist:
#         raise DjangoValidationError(
#             {'reversal_date': _("No open accounting period found for reversal date %(date)s.") % {'date': effective_reversal_date}}
#         )
#
#     # 3. Create New Voucher Header (starts in DRAFT)
#     reversing_voucher = Voucher(
#         date=effective_reversal_date,
#         effective_date=effective_reversal_date, # Usually same as date for reversals
#         narration=_(f"Reversal of Voucher: {original_voucher.voucher_number}. Original: {original_voucher.narration or ''}")[:255], # Ensure max_length
#         voucher_type=reversal_voucher_type,
#         status=TransactionStatus.DRAFT.name, # Always start draft
#         party=original_voucher.party,
#         accounting_period=reversal_period,
#         reference=f"Reversal of {original_voucher.voucher_number}"[:100], # Ensure max_length
#         # Add FK link if exists: reversed_original_voucher=original_voucher,
#     )
#     reversing_voucher.save() # Save header first
#
#     # 4. Create Reversed Lines
#     new_lines = []
#     for line in original_voucher.lines.all():
#         # Ensure account is still active and allows posting for the reversal
#         if not line.account or not line.account.is_active or not line.account.allow_direct_posting:
#              logger.error(f"Skipping reversal line for inactive/non-postable account {line.account} from original voucher {original_voucher_id}")
#              # Or raise ValidationError if reversal requires all accounts to be valid
#              raise DjangoValidationError(f"Cannot reverse line using inactive/non-postable account: {line.account}")
#
#         new_lines.append(VoucherLine(
#             voucher=reversing_voucher,
#             account=line.account,
#             dr_cr=(DrCrType.CREDIT.name if line.dr_cr == DrCrType.DEBIT.name else DrCrType.DEBIT.name),
#             amount=line.amount,
#             narration=f"Reversal - {line.narration or ''}"[:255] # Ensure max_length
#         ))
#
#     if not new_lines:
#          logger.warning(f"Original voucher {original_voucher_id} has no valid lines to reverse.")
#          # Clean up? Delete reversing_voucher? Or allow empty reversal (unlikely)?
#          raise VoucherWorkflowError(_("Original voucher has no lines or uses inactive accounts, cannot reverse."))
#     else:
#         VoucherLine.objects.bulk_create(new_lines)
#
#     reversing_voucher.refresh_from_db() # Reload to get lines associated
#
#     # 5. Handle Posting or Leave as Draft
#     final_status = TransactionStatus.DRAFT.name
#     log_action = ApprovalActionType.COMMENTED.name # Default log action for draft creation
#     log_comment = f"Reversing voucher created in Draft for Original {original_voucher.voucher_number}."
#
#     if post_immediately:
#         _check_permission(user, 'post_voucher') # Check permission for direct posting
#
#         # Assign number *before* validation for posting
#         try:
#             assign_voucher_number(reversing_voucher) # Assigns number to instance
#         except DjangoValidationError as e:
#              logger.error(f"Voucher numbering failed during reversal posting for Original Voucher PK {original_voucher.pk}: {e}", exc_info=True)
#              raise VoucherWorkflowError(_("Failed to assign voucher number during reversal posting.")) from e
#
#         # Validate the *reversing* voucher before posting
#         _validate_voucher_for_posting(reversing_voucher)
#
#         final_status = TransactionStatus.POSTED.name
#         reversing_voucher.status = final_status
#         reversing_voucher.updated_at = timezone.now()
#         reversing_voucher.save(update_fields=['status', 'voucher_number', 'updated_at']) # Save status & number
#
#         _trigger_balance_updates(reversing_voucher) # Trigger balance update for the reversal
#
#         log_action = ApprovalActionType.APPROVED.name # Log as approved if posted directly
#         log_comment = f"Automatic posting of reversal for {original_voucher.voucher_number}."
#         logger.info(f"Posting successful: Reversing Voucher {reversing_voucher.voucher_number} for Original {original_voucher.voucher_number}")
#     else:
#         logger.info(f"Reversal Created: Voucher {reversing_voucher.pk} in Draft for Original {original_voucher.voucher_number}")
#
#     # 6. Log the creation/posting action for the *reversing* voucher
#     _log_approval_action(
#         voucher=reversing_voucher, user=user, action_type=log_action,
#         from_status=TransactionStatus.DRAFT.name, # It starts as Draft
#         to_status=final_status,
#         comments=log_comment
#     )
#
#     # Optional: Update original voucher if it has e.g. `is_reversed = models.BooleanField()`
#     # original_voucher.is_reversed = True
#     # original_voucher.save(update_fields=['is_reversed'])
#
#     return reversing_voucher
#
#
# # --- Internal Helper/Validation Functions ---
#
# def _validate_voucher_essentials(voucher: Voucher):
#     """Basic checks before submitting/posting. Raises custom exceptions or DjangoValidationError."""
#     # 1. Balance Check
#     if not voucher.is_balanced:
#         raise BalanceError() # Custom exception indicating imbalance
#
#     # 2. Period Lock Check
#     # Ensure period is loaded if called outside a select_related context
#     period = voucher.accounting_period
#     if hasattr(period, 'locked') and period.locked: # Check if loaded and locked
#         raise PeriodLockedError(period_name=str(period))
#     elif not hasattr(period, 'locked'): # Fetch if not loaded (shouldn't happen with select_related)
#          period = AccountingPeriod.objects.get(pk=voucher.accounting_period_id)
#          if period.locked:
#              raise PeriodLockedError(period_name=str(period))
#
#     # 3. Date within Period Check
#     if not (period.start_date <= voucher.date <= period.end_date):
#         raise DjangoValidationError(
#             {'date': _("Voucher date %(v_date)s is outside the accounting period '%(p_name)s' (%(p_start)s to %(p_end)s).") %
#              {'v_date': voucher.date, 'p_name': str(period), 'p_start': period.start_date, 'p_end': period.end_date}}
#         )
#
#     # 4. Lines Exist and Have Accounts Check
#     if not voucher.lines.exists(): # Use exists() for efficiency
#          raise DjangoValidationError({'lines': _("Voucher must have at least one line item.")})
#     if voucher.lines.filter(account__isnull=True).exists():
#         raise DjangoValidationError({'lines': _("One or more voucher lines is missing an account.")})
#     # 5. Check if line accounts allow direct posting
#     if voucher.lines.filter(account__allow_direct_posting=False).exists():
#         raise DjangoValidationError({'lines': _("One or more lines uses an account that does not allow direct posting.")})
#
#
# def _validate_voucher_for_posting(voucher: Voucher):
#     """Comprehensive validation specifically before changing status to POSTED."""
#     _validate_voucher_essentials(voucher) # Run all basic checks first
#
#     # Add any stricter checks only required for final posting:
#     if not voucher.voucher_number:
#          # Should have been assigned by submit or reversal logic before calling this
#          logger.error(f"Attempting to post Voucher PK {voucher.pk} without a voucher number.")
#          raise VoucherWorkflowError(_("Voucher number is missing. Cannot post."))
#
#     # Example: Check against Party credit limit (requires fetching party details)
#     # if voucher.voucher_type == VoucherType.SALES.name and voucher.party_id:
#     #     # Fetch party and check limit here...
#     #     pass
#
#     logger.debug(f"Pre-Posting Validation Passed: Voucher {voucher.voucher_number}")
#
#
# def _trigger_balance_updates(voucher: Voucher):
#     """Dispatches the task to update ledger balances."""
#     if voucher.status != TransactionStatus.POSTED.name:
#         logger.warning(f"Attempted to trigger balance update for non-posted Voucher {voucher.pk} (Status: {voucher.status}). Skipping.")
#         return
#
#     logger.info(f"Triggering balance updates for POSTED Voucher {voucher.voucher_number or voucher.pk}")
#     try:
#         # Task should handle idempotency internally (e.g., check voucher.balances_updated flag)
#         update_account_balances_task.delay(voucher_id=voucher.pk)
#         logger.debug(f"Enqueued balance update task for Voucher PK: {voucher.pk}")
#     except Exception as e:
#         # Critical error - balances will be inconsistent if task fails permanently
#         logger.critical(
#             f"ALERT: Failed to enqueue balance update task for Voucher {voucher.pk} ({voucher.voucher_number}). "
#             f"Balance inconsistency likely. Error: {e}", exc_info=True
#         )
#
#
# def _log_approval_action(voucher: Voucher, user: settings.AUTH_USER_MODEL, action_type: str, from_status: str, to_status: str, comments: str):
#      """Helper to create VoucherApproval log entries."""
#      try:
#          VoucherApproval.objects.create(
#              voucher=voucher,
#              user=user,
#              action_type=action_type, # Pass the string name from the enum
#              from_status=from_status,
#              to_status=to_status,
#              comments=comments
#          )
#          logger.debug(f"Logged action '{action_type}' for Voucher {voucher.pk} by user '{user.get_username()}'")
#      except Exception as e:
#          # Log error but don't necessarily fail the parent transaction unless essential
#          logger.error(f"Failed to log approval action {action_type} for Voucher {voucher.pk}: {e}", exc_info=True)