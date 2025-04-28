import logging
from decimal import Decimal
from celery import shared_task
from django.db import transaction, OperationalError
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

# --- Model Imports ---
# Ensure these paths are correct for your project structure
try:
    from .models.journal import Voucher, VoucherLine, DrCrType, TransactionStatus
    from .models.coa import Account, AccountType
except ImportError as e:
    raise ImportError(f"Could not import necessary models for tasks.py. Check paths and dependencies: {e}")


logger = logging.getLogger(__name__)

# --- Constants ---
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 60
ZERO_DECIMAL = Decimal('0.00')

# --- Corrected Helper Methods ---
# These functions MUST compare against the database VALUE of the AccountType enum
def _account_affects_balance_positively_on_credit(account_type_value: str) -> bool:
    """Checks if credits increase the balance for this account type VALUE."""
    return account_type_value in [
        AccountType.LIABILITY.value,
        AccountType.EQUITY.value,
        AccountType.INCOME.value
    ]

def _account_affects_balance_positively_on_debit(account_type_value: str) -> bool:
    """Checks if debits increase the balance for this account type VALUE."""
    return account_type_value in [
        AccountType.ASSET.value,
        AccountType.EXPENSE.value,
        AccountType.COST_OF_GOODS_SOLD.value # Ensure COGS value is included
    ]
# --- End Corrected Helper Methods ---


# --- Asynchronous Task ---

@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=RETRY_DELAY_SECONDS,
             name="crp_accounting.update_account_balances") # Optional: Give the task a specific name
def update_account_balances_task(self, voucher_id: int):
    """
    Asynchronous Celery task to update account balances based on a posted voucher.

    Uses the `balances_updated` flag on the Voucher model for idempotency.
    Handles retries for operational database errors.
    """
    task_id = self.request.id or "sync_run" # Handle eager runs without ID
    logger.info(f"[Task:{task_id}] Starting balance update check for Voucher ID: {voucher_id}")

    # --- Idempotency Check ---
    # CRITICAL: Assumes Voucher model has a BooleanField named `balances_updated`.
    # Add this field via migration if it doesn't exist.
    try:
        voucher_base_qs = Voucher.objects.filter(pk=voucher_id)
        if not hasattr(Voucher, 'balances_updated'):
             logger.critical(f"[Task:{task_id}] CRITICAL ERROR: Voucher model is missing the 'balances_updated' field required for idempotency. Aborting task for voucher {voucher_id}.")
             # Do not retry - this is a code/model definition issue.
             return

        if voucher_base_qs.filter(balances_updated=True).exists():
            logger.info(f"[Task:{task_id}] Skipping Voucher {voucher_id}: Balances already marked as updated.")
            return # Successfully skipped, task completes normally

    except OperationalError as oe_check:
        logger.warning(f"[Task:{task_id}] DB operational error during idempotency check for Voucher {voucher_id}: {oe_check}. Retrying...")
        raise self.retry(exc=oe_check) # Use Celery's retry mechanism
    except Exception as e_check:
        logger.error(f"[Task:{task_id}] Unexpected error during idempotency check for Voucher {voucher_id}: {e_check}. Failing task.", exc_info=True)
        return # Fail if check fails unexpectedly
    # --- End Idempotency Check ---

    # If we passed the check, proceed with fetching and processing
    try:
        # Fetch voucher with related lines and accounts
        # Select related account for efficiency within the loop
        voucher = Voucher.objects.prefetch_related('lines__account').get(pk=voucher_id)

        # Safety check: Ensure voucher is POSTED.
        if voucher.status != TransactionStatus.POSTED:
             logger.warning(f"[Task:{task_id}] Voucher {voucher_id} is not POSTED (Status: {voucher.status}). Skipping balance update.")
             # Ensure flag is False if status is wrong (should be handled by model save ideally)
             if voucher.balances_updated:
                 logger.warning(f"[Task:{task_id}] Resetting balances_updated flag for non-POSTED voucher {voucher_id}.")
                 Voucher.objects.filter(pk=voucher_id).update(balances_updated=False)
             return

        # If balances_updated is False for a POSTED voucher, proceed.

        # Process all lines within a single database transaction
        with transaction.atomic():
            processed_accounts = set()
            logger.debug(f"[Task:{task_id}] Starting atomic balance update for Voucher {voucher_id}.")
            # Get timestamp once before the loop for consistency
            current_time = timezone.now()

            for line in voucher.lines.all():
                # --- Line Validation ---
                if not line.account_id or line.amount is None or line.amount == ZERO_DECIMAL:
                    logger.warning(f"[Task:{task_id}] Skipping invalid VoucherLine {line.pk} (Account: {line.account_id}, Amount: {line.amount}) for Voucher {voucher_id}")
                    continue

                account = line.account
                account_pk = account.pk

                try:
                    # Lock the specific account row for update
                    acc_to_update = Account.objects.select_for_update().get(pk=account_pk)

                    # Initialize balance if it's None (safer than erroring)
                    if acc_to_update.current_balance is None:
                        logger.warning(f"[Task:{task_id}] Account {account_pk} had NULL balance. Initializing to 0.")
                        acc_to_update.current_balance = ZERO_DECIMAL

                    original_balance = acc_to_update.current_balance
                    adjustment = line.amount

                    # --- Apply Adjustment using CORRECTED Helpers ---
                    # Ensure comparison uses the VALUE stored in the fields
                    if line.dr_cr == DrCrType.DEBIT.value:
                        if _account_affects_balance_positively_on_debit(acc_to_update.account_type):
                            acc_to_update.current_balance += adjustment
                        else:
                            acc_to_update.current_balance -= adjustment
                    elif line.dr_cr == DrCrType.CREDIT.value:
                        if _account_affects_balance_positively_on_credit(acc_to_update.account_type):
                            acc_to_update.current_balance += adjustment
                        else:
                            acc_to_update.current_balance -= adjustment
                    else:
                        logger.error(f"[Task:{task_id}] Invalid DrCrType '{line.dr_cr}' on VoucherLine {line.pk}.")
                        # Skip this line, but don't fail the whole transaction necessarily
                        continue
                    # --- End Apply Adjustment ---

                    # --- Set timestamp and Save Account ---
                    acc_to_update.balance_last_updated = current_time
                    acc_to_update.save(
                        update_fields=['current_balance', 'balance_last_updated']
                    )
                    # --- End Set timestamp and Save Account ---

                    processed_accounts.add(account_pk)
                    logger.debug(f"[Task:{task_id}] Updated balance Account {account_pk}: {original_balance} -> {acc_to_update.current_balance} (Line {line.pk}, Time: {current_time})")

                # --- Error Handling for Single Account Update ---
                except Account.DoesNotExist:
                     # This account linked to the line doesn't exist, log and continue with other lines
                     logger.error(f"[Task:{task_id}] Account {account_pk} referenced by VoucherLine {line.pk} not found during update!")
                except OperationalError as oe_acct:
                     # Could be lock contention on this specific account row
                     logger.warning(f"[Task:{task_id}] DB lock error updating Account {account_pk}: {oe_acct}. Retrying entire task.")
                     # Retry the whole task because the atomic block needs to succeed entirely
                     raise self.retry(exc=oe_acct)
                except Exception as e_acct:
                     # Catch other errors during this specific account update
                     logger.exception(f"[Task:{task_id}] Unexpected error updating Account {account_pk} (Line {line.pk}): {e_acct}")
                     # Reraise the exception to ensure the transaction.atomic() block rolls back
                     raise
            # --- End For Loop over Lines ---

            # Transaction commits here if no exceptions were raised within the 'with' block
            logger.debug(f"[Task:{task_id}] Atomic balance update transaction completed successfully for Voucher {voucher_id}.")

        # --- Mark Voucher as Updated (AFTER successful transaction commit) ---
        # This happens only if the `with transaction.atomic()` block succeeded without raising an error.
        try:
            # Use direct update for efficiency, avoids model save signals.
            # Ensure 'updated_at' is also updated on the voucher itself.
            rows_updated = Voucher.objects.filter(pk=voucher_id).update(
                balances_updated=True,
                updated_at=current_time # Use same timestamp as account updates
            )
            if rows_updated > 0:
                logger.info(f"[Task:{task_id}] Marked Voucher {voucher_id} balances as updated.")
            else:
                # This could happen if the voucher was deleted *after* the transaction committed
                # but *before* this update ran (unlikely but possible).
                logger.warning(f"[Task:{task_id}] Could not mark Voucher {voucher_id} as updated (rows affected: {rows_updated}). It might have been deleted concurrently.")
        except OperationalError as oe_mark:
             # Handle potential DB issues during the marking phase
             logger.error(f"[Task:{task_id}] ALERT: DB error marking Voucher {voucher_id} as updated: {oe_mark}. Balances WERE updated. Manual check needed.")
             # Do NOT retry here, balances already committed. Log and alert.
        except Exception as e_mark:
            # CRITICAL: Balances *were* updated, but marking failed for other reason.
            logger.critical(f"[Task:{task_id}] ALERT: Balances updated for Voucher {voucher_id}, BUT MARKING FAILED: {e_mark}. Manual check needed to prevent potential duplicate processing on retry.", exc_info=True)
            # Do NOT retry here, balances already committed. Log and alert.

        logger.info(f"[Task:{task_id}] Finished balance update task for Voucher ID: {voucher_id}. Accounts processed: {len(processed_accounts)}")

    # --- Outer Exception Handling ---
    except ObjectDoesNotExist:
        # Voucher not found initially - Cannot proceed.
        logger.error(f"[Task:{task_id}] Voucher with ID {voucher_id} not found. Cannot update balances.")
        # No retry needed if object fundamentally doesn't exist.
    except OperationalError as oe_task:
        # Catch DB errors at the task level (e.g., during initial voucher fetch)
        logger.warning(f"[Task:{task_id}] Database operational error during task execution for Voucher {voucher_id}: {oe_task}. Retrying...")
        raise self.retry(exc=oe_task) # Rely on Celery's retry mechanism
    except Exception as e_task:
        # Catch all other unexpected errors at the task level
        logger.exception(f"[Task:{task_id}] Unexpected error processing balance update for Voucher {voucher_id}: {e_task}")
        # Retry for generic errors based on Celery's default policy
        try:
            # Celery handles retry based on decorator settings (max_retries, etc.)
            raise self.retry(exc=e_task)
        except self.MaxRetriesExceededError:
            logger.critical(f"[Task:{task_id}] Max retries exceeded for Voucher {voucher_id}. Balance update failed permanently. ALERTING NEEDED.")
        except Exception as retry_e:
             # Catch potential errors during the retry call itself
             logger.error(f"[Task:{task_id}] Error attempting to retry task for Voucher {voucher_id}: {retry_e}")