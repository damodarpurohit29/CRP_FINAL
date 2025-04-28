# crp_accounting/services/ledger_service.py

import logging
from decimal import Decimal
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple, Union

from django.db import models
from django.db.models import Sum, Q, F, Case, When, Value, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist
from django.core.cache import cache # Import Django's cache framework
from django.conf import settings # To potentially make timeout configurable

# --- Model Imports ---
from ..models.journal import Voucher, VoucherLine, DrCrType, TransactionStatus
from ..models.coa import Account
from crp_core.enums import AccountNature

logger = logging.getLogger(__name__)

# --- Constants ---
# Cache timeout for opening balance in seconds (e.g., 15 minutes).
# Can be overridden in Django settings.py: CACHE_OPENING_BALANCE_TIMEOUT = 900
CACHE_OPENING_BALANCE_TIMEOUT = getattr(settings, 'CACHE_OPENING_BALANCE_TIMEOUT', 900)


# =============================================================================
# Ledger Service Functions
# =============================================================================

def calculate_account_balance_upto(account: Account, date_exclusive: Optional[date]) -> Decimal:
    """
    Calculates the closing balance for an account based on all POSTED transactions
    strictly *before* a specified date (exclusive).

    Uses basic time-based caching to optimize repeated calculations for the same
    account and date.

    Args:
        account: The Account instance.
        date_exclusive: The date before which transactions should be considered.
                        If None, the balance is 0.

    Returns:
        Decimal: The calculated balance before the specified date.

    Raises:
        ValueError: If the account's nature is misconfigured.
    """
    if not date_exclusive:
        logger.debug(f"Calculating balance up to None for Account {account.pk}, returning 0.")
        return Decimal('0.00')

    # --- Caching Logic ---
    # Generate a unique cache key based on account and date.
    # Using ISO format ensures consistent date representation.
    cache_key = f"acc_ob_{account.pk}_{date_exclusive.isoformat()}"
    cached_balance = cache.get(cache_key)

    if cached_balance is not None:
        logger.debug(f"Cache HIT for opening balance: Key='{cache_key}', Account={account.pk}, Date={date_exclusive}")
        # Ensure the cached value is returned as a Decimal
        return Decimal(cached_balance)
    # --- End Caching Logic ---

    logger.debug(f"Cache MISS for opening balance: Key='{cache_key}', Account={account.pk}, Date={date_exclusive}. Calculating...")

    # Fetch relevant lines if not found in cache
    lines = VoucherLine.objects.filter(
        account=account,
        voucher__status=TransactionStatus.POSTED,
        voucher__date__lt=date_exclusive
    )

    aggregation = lines.aggregate(
        total_debit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.DEBIT.name)),
            Decimal('0.00'),
            output_field=models.DecimalField()
        ),
        total_credit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.CREDIT.name)),
            Decimal('0.00'),
            output_field=models.DecimalField()
        )
    )
    debit_total = aggregation['total_debit']
    credit_total = aggregation['total_credit']

    # Calculate balance based on account nature
    if account.account_nature == AccountNature.DEBIT.name:
        balance = debit_total - credit_total
    elif account.account_nature == AccountNature.CREDIT.name:
        balance = credit_total - debit_total
    else:
        logger.error(f"Account {account} (PK: {account.pk}) has unexpected account nature '{account.account_nature}'.")
        raise ValueError(f"Invalid account nature '{account.account_nature}' configured for account {account.account_number}.")

    # --- Store calculated balance in cache ---
    try:
        # Store as string to avoid potential float precision issues with some backends,
        # although Decimal should generally be fine with most modern backends.
        # Converting back to Decimal on retrieval ensures type consistency.
        cache.set(cache_key, str(balance), timeout=CACHE_OPENING_BALANCE_TIMEOUT)
        logger.debug(f"Cached opening balance for Key='{cache_key}': {balance} (Timeout: {CACHE_OPENING_BALANCE_TIMEOUT}s)")
    except Exception as e:
        # Log caching errors but don't fail the main calculation
        logger.error(f"Failed to cache opening balance for Key='{cache_key}': {e}", exc_info=True)
    # --- End Store in Cache ---

    logger.debug(f"Calculated balance up to {date_exclusive} for Account {account.pk}: {balance}")
    return balance


def get_account_ledger_data(
    account_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Dict[str, Union[Account, Decimal, List[Dict], Optional[date]]]:
    """
    Retrieves detailed ledger transaction history for a specific account
    within an optional date range. Leverages caching for opening balance calculation.

    (Docstring remains the same as previous version regarding Args, Returns, Raises)
    """
    try:
        account = Account.objects.select_related('account_group').get(pk=account_id)
    except Account.DoesNotExist:
        logger.error(f"Ledger requested for non-existent Account ID: {account_id}")
        raise ObjectDoesNotExist(f"Account with ID {account_id} not found.")

    logger.info(f"Generating ledger for Account: {account} ({account_id}) | Period: {start_date or 'Beginning'} to {end_date or 'End'}")

    # --- 1. Calculate Opening Balance (Now uses caching internally) ---
    opening_balance = calculate_account_balance_upto(account, start_date)
    # Logging already handled within calculate_account_balance_upto

    # --- 2. Fetch Ledger Entries within the Period ---
    ledger_lines_query = VoucherLine.objects.filter(
        account=account,
        voucher__status=TransactionStatus.POSTED
    ).select_related('voucher').order_by(
        'voucher__date', 'voucher__created_at', 'pk'
    )

    if start_date:
        ledger_lines_query = ledger_lines_query.filter(voucher__date__gte=start_date)
    if end_date:
        ledger_lines_query = ledger_lines_query.filter(voucher__date__lte=end_date)

    ledger_lines = list(ledger_lines_query)

    # --- 3. Process Entries and Calculate Running Balance & Period Totals ---
    entries: List[Dict] = []
    running_balance: Decimal = opening_balance
    period_total_debit: Decimal = Decimal('0.00')
    period_total_credit: Decimal = Decimal('0.00')
    is_debit_nature_account: bool = account.is_debit_nature

    for line in ledger_lines:
        debit_amount = line.amount if line.dr_cr == DrCrType.DEBIT.name else Decimal('0.00')
        credit_amount = line.amount if line.dr_cr == DrCrType.CREDIT.name else Decimal('0.00')

        balance_change = Decimal('0.00')
        if debit_amount > Decimal('0.00'):
            balance_change = debit_amount if is_debit_nature_account else -debit_amount
            period_total_debit += debit_amount
        elif credit_amount > Decimal('0.00'):
            balance_change = -credit_amount if is_debit_nature_account else credit_amount
            period_total_credit += credit_amount

        running_balance += balance_change

        entries.append({
            'line_pk': line.pk,
            'date': line.voucher.date,
            'voucher_pk': line.voucher.pk,
            'voucher_number': line.voucher.voucher_number,
            'narration': line.voucher.narration or line.narration or '',
            'reference': line.voucher.reference or '',
            'debit': debit_amount,
            'credit': credit_amount,
            'running_balance': running_balance
        })

    closing_balance = running_balance
    logger.debug(f"Closing Balance (at end of {end_date or 'period'}): {closing_balance}")
    logger.debug(f"Period Totals for Account {account.pk}: Debit={period_total_debit}, Credit={period_total_credit}")

    # --- 4. Assemble Final Response Data ---
    return {
        'account': account,
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_balance,
        'total_debit': period_total_debit,
        'total_credit': period_total_credit,
        'entries': entries,
        'closing_balance': closing_balance,
    }