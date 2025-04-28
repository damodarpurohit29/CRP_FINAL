# crp_accounting/services/voucher_utils.py (CORRECTED)

import logging
from django.db import transaction, IntegrityError
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError as DjangoValidationError

# --- Model & Service Imports ---
from ..models.journal import Voucher, VoucherSequence
from ..models.period import AccountingPeriod
from .sequence_service import get_or_create_sequence_config
# from ..exceptions import VoucherWorkflowError, PeriodLockedError # Optional custom exceptions

logger = logging.getLogger(__name__)

# --- Main Public Function ---

def assign_voucher_number(voucher_instance: Voucher):
    """
    Generates and assigns the next automatic voucher number if needed.

    - Checks if a voucher number already exists; if so, it returns early.
    - Proceeds with automatic generation only if `voucher_number` is blank.

    Designed to be called from Voucher.save() under appropriate conditions.

    Args:
        voucher_instance: The Voucher instance. Period and Type MUST be set.

    Raises:
        DjangoValidationError: If prerequisites are missing, period is locked,
                               or generation fails due to configuration/database issues.
    """
    # --- Handle Pre-existing Numbers ---
    if voucher_instance.voucher_number:
        logger.debug(f"Voucher number '{voucher_instance.voucher_number}' already exists for Voucher {voucher_instance.pk}. Skipping generation.")
        return # Number already exists, nothing to do.

    # --- Proceed with Automatic Generation ---
    # If we reach here, voucher_number is blank.
    logger.info(f"Proceeding with automatic voucher number generation for Voucher {voucher_instance.pk}")

    # 1. Validate Prerequisites (Type and Period must be set for sequence lookup)
    _validate_voucher_prerequisites(voucher_instance) # Raises DjangoValidationError

    period = voucher_instance.accounting_period # Get related object

    # 2. Check Period Lock (Still relevant for auto-generation timing)
    if period.locked:
        logger.warning(f"Attempt to generate voucher number for locked period {str(period)} for Voucher PK {voucher_instance.pk}")
        # Consider using custom PeriodLockedError here if defined
        raise DjangoValidationError(
             _("Cannot generate voucher number: Accounting Period '%(period_name)s' is locked.") % {'period_name': str(period)}
        )

    # 3. Perform Atomic Increment and Formatting
    try:
        sequence_config, next_num = _increment_sequence_and_get_next_number(
            voucher_instance.voucher_type, period
        )
        formatted_number = sequence_config.format_number(next_num)

        # 4. Assign the generated number back to the instance
        # The actual saving of the instance happens outside this function (in Voucher.save)
        voucher_instance.voucher_number = formatted_number

        logger.info(
            "Assigned auto-generated voucher number '%s' to Voucher PK %s (%s / %s)",
            formatted_number, voucher_instance.pk, voucher_instance.voucher_type, str(period)
        )
    except (IntegrityError, DjangoValidationError) as e:
        logger.error(
            "Failed during auto-generation process for Voucher PK %s (Type=%s, Period=%s): %s",
            voucher_instance.pk, voucher_instance.voucher_type, period.name, e
        )
        raise DjangoValidationError(_("Failed to generate voucher number. Please check sequence configuration or try again.")) from e
    except Exception as e:
        logger.exception(
            "Unexpected error during auto-generation for Voucher PK %s (Type=%s, Period=%s): %s",
             voucher_instance.pk, voucher_instance.voucher_type, str(period), e
        )
        raise DjangoValidationError(_("An unexpected system error occurred during voucher number generation.")) from e

# --- Helper Functions (Unchanged) ---

def _validate_voucher_prerequisites(voucher_instance: Voucher):
    """Ensure necessary fields are set on the Voucher for number generation."""
    if not voucher_instance.voucher_type:
        raise DjangoValidationError({'voucher_type': _("Voucher type must be set before generating voucher number.")})
    if not voucher_instance.accounting_period_id:
        raise DjangoValidationError({'accounting_period': _("Accounting period must be set before generating voucher number.")})

@transaction.atomic
def _increment_sequence_and_get_next_number(voucher_type: str, period: AccountingPeriod) -> tuple[VoucherSequence, int]:
    """
    Atomically retrieves sequence config, increments its counter using DB lock,
    saves it, and returns the config object and the *new* number.
    (Implementation remains the same)
    """
    # ... (existing implementation of _increment_sequence_and_get_next_number) ...
    try:
        sequence_config = get_or_create_sequence_config(voucher_type, period)
        locked_sequence = VoucherSequence.objects.select_for_update().get(pk=sequence_config.pk)
        next_number = locked_sequence.last_number + 1
        locked_sequence.last_number = next_number
        locked_sequence.save(update_fields=['last_number', 'updated_at'])
        return locked_sequence, next_number
    except VoucherSequence.DoesNotExist:
        logger.error(f"VoucherSequence row disappeared unexpectedly for Type={voucher_type}, Period={str(period)} during atomic increment.")
        raise DjangoValidationError(_("Sequence configuration lock failed unexpectedly."))
    except Exception as e:
        logger.exception(f"Error during atomic sequence increment for Type=%s, Period=%s: {e}", voucher_type, str(period), e)
        raise