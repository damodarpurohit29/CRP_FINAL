import logging
import math
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ImproperlyConfigured

# Use relative imports if services are in the same app level
from ..models.journal import VoucherSequence # Import the model
from ..models.period import AccountingPeriod
# from crp_core.enums import VoucherType # Only needed if type checking VoucherType

logger = logging.getLogger(__name__)

def _calculate_quarter(date_obj):
    """Calculates the fiscal quarter (1-4) for a given date."""
    if not date_obj:
        return "NODATE" # Handle cases where period might lack a start date initially
    return math.ceil(date_obj.month / 3.0)

def _get_default_prefix(voucher_type: str, period: AccountingPeriod) -> str:
    """
    Generates a sensible default prefix for a voucher sequence.
    Example: JV-2024Q1-
    """
    if not period or not period.start_date:
         logger.warning("Cannot generate default prefix: AccountingPeriod or start_date missing.")
         # Return a generic prefix or raise error, depending on policy
         return f"{voucher_type[:2].upper()}-DEF-"

    # Correctly calculate quarter
    quarter = _calculate_quarter(period.start_date)
    # Format: Prefix-YearQ#- e.g., JV-2024Q1-
    period_code = period.start_date.strftime('%Y') + f"Q{quarter}"
    return f"{voucher_type[:2].upper()}-{period_code}-"

def get_or_create_sequence_config(voucher_type: str, period: AccountingPeriod) -> VoucherSequence:
    """
    Retrieves the VoucherSequence configuration for the given scope,
    or creates it with default settings if it doesn't exist.

    This function handles getting the configuration row, not the atomic increment.

    Args:
        voucher_type: The voucher type identifier (e.g., 'GENERAL', 'SALES').
        period: The AccountingPeriod instance.

    Returns:
        The VoucherSequence instance for the given scope.

    Raises:
        ImproperlyConfigured: If period is None.
        Exception: For database errors during get_or_create.
    """
    if not period:
        # This should ideally be caught earlier, but good to have a check
        raise ImproperlyConfigured("AccountingPeriod cannot be None when getting/creating sequence config.")

    try:
        sequence_config, created = VoucherSequence.objects.get_or_create(
            voucher_type=voucher_type,
            accounting_period=period,
            defaults={
                'prefix': _get_default_prefix(voucher_type, period),
                'padding_digits': 4, # Default padding
                'last_number': 0
            }
        )
        if created:
            logger.info(
                "Created new VoucherSequence config for Type=%s, Period=%s with prefix '%s'",
                voucher_type, str(period), sequence_config.prefix
            )
        return sequence_config
    except Exception as e:
        logger.exception(
            "Database error getting/creating VoucherSequence for Type=%s, Period=%s: %s",
            voucher_type, str(period), e
        )
        # Re-raise the exception to be handled by the calling function
        raise