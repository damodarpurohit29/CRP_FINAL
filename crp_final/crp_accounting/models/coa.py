# crp_accounting/models/coa.py

# crp_accounting/models/coa.py

import logging
from decimal import Decimal
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.utils import timezone # Needed for balance_last_updated

# Assuming enums are defined correctly in crp_core/enums.py
# Ensure these enums exist and are properly defined.
from crp_core.enums import AccountType, AccountNature, CurrencyType, PartyType, DrCrType

logger = logging.getLogger(__name__)

# --- Constants ---
# --- CORRECTED Dictionary: Using Enum VALUES as Keys ---
# This dictionary is used by Account.save() to determine the nature.
# The keys MUST match the values stored in the Account.account_type field.
ACCOUNT_TYPE_TO_NATURE = {
    AccountType.ASSET.value: AccountNature.DEBIT.name,           # e.g., 'ASSET': 'DEBIT'
    AccountType.LIABILITY.value: AccountNature.CREDIT.name,      # e.g., 'LIABILITY': 'CREDIT'
    AccountType.EQUITY.value: AccountNature.CREDIT.name,         # e.g., 'EQUITY': 'CREDIT'
    AccountType.INCOME.value: AccountNature.CREDIT.name,         # e.g., 'INCOME': 'CREDIT'
    AccountType.EXPENSE.value: AccountNature.DEBIT.name,         # e.g., 'EXPENSE': 'DEBIT'
    AccountType.COST_OF_GOODS_SOLD.value: AccountNature.DEBIT.name, # e.g., 'COGS': 'DEBIT' <-- Corrected Key
}


# =============================================================================
# P&L Section Enum
# =============================================================================
class PLSection(models.TextChoices):
    """
    Defines sections for structuring the Profit & Loss statement.
    Allows for standard reporting like Gross Profit calculation.
    """
    REVENUE = 'REVENUE', _('Revenue')
    COGS = 'COGS', _('Cost of Goods Sold')
    OPERATING_EXPENSE = 'OPERATING_EXPENSE', _('Operating Expense')
    OTHER_INCOME = 'OTHER_INCOME', _('Other Income')
    OTHER_EXPENSE = 'OTHER_EXPENSE', _('Other Expense')
    TAX_EXPENSE = 'TAX_EXPENSE', _('Tax Expense')
    DEPRECIATION_AMORTIZATION = 'DEPR_AMORT', _('Depreciation & Amortization')
    NONE = 'NONE', _('Not Applicable (Balance Sheet)') # Default for non-P&L accounts

# =============================================================================
# Account Group Model
# =============================================================================
class AccountGroup(models.Model):
    """
    Represents a hierarchical grouping for the Chart of Accounts (COA).
    Allows structuring accounts into logical categories for reporting and organization.
    """
    name = models.CharField(
        _("Group Name"),
        max_length=150,
        unique=True,
        db_index=True,
        help_text=_("Unique name for the account group (e.g., Current Assets, Operating Expenses).")
    )
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional description of the account group's purpose.")
    )
    parent_group = models.ForeignKey(
        'self',
        verbose_name=_("Parent Group"),
        on_delete=models.PROTECT, # Prevent deleting a group if it has sub-groups
        null=True,
        blank=True,
        related_name='sub_groups',
        help_text=_("Assign parent for hierarchy. Leave blank for top-level.")
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True, editable=False)

    class Meta:
        verbose_name = _('Account Group')
        verbose_name_plural = _('Account Groups')
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_all_child_accounts(self):
        """Recursively gets all accounts under this group and its sub-groups."""
        accounts = list(self.accounts.all())
        for sub_group in self.sub_groups.all():
            accounts.extend(sub_group.get_all_child_accounts())
        return accounts

# =============================================================================
# Account Model
# =============================================================================
class Account(models.Model):
    """
    Represents a specific ledger account within the Chart of Accounts (COA).
    Transactions are posted here (if allowed). Defines classification and nature.
    Stores the calculated current balance.
    """
    # --- Identification ---
    account_number = models.CharField(
        _("Account Number"),
        max_length=50,
        unique=True,
        db_index=True,
        help_text=_("Unique identifier code for the account (e.g., 10100, 40001).")
    )
    account_name = models.CharField(
        _("Account Name"),
        max_length=255,
        db_index=True,
        help_text=_("Human-readable name (e.g., Cash On Hand, Sales Revenue - Services).")
    )
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional detailed description of the account's purpose.")
    )

    # --- Classification & Hierarchy ---
    account_group = models.ForeignKey(
        AccountGroup,
        verbose_name=_("Account Group"),
        on_delete=models.PROTECT,
        related_name='accounts',
        help_text=_("The hierarchical group this account belongs to.")
    )
    account_type = models.CharField(
        _("Account Type"),
        max_length=20, # Should match the longest value in AccountType (e.g., 'LIABILITY')
        choices=AccountType.choices,
        db_index=True,
        help_text=_("Fundamental accounting classification (Asset, Liability, etc.).")
    )
    account_nature = models.CharField(
        _("Account Nature"),
        max_length=10, # Should match 'DEBIT' or 'CREDIT'
        choices=AccountNature.choices,
        editable=False,
        help_text=_("System-inferred nature (Debit/Credit). Based on Account Type.")
    )
    pl_section = models.CharField(
        _("P&L Section"),
        max_length=25, # Should match the longest value in PLSection
        choices=PLSection.choices,
        default=PLSection.NONE,
        blank=True,
        db_index=True,
        help_text=_("Specific section classification for the Profit & Loss statement (e.g., Revenue, COGS, Operating Expense). Required for detailed P&L structure.")
    )

    # --- Settings & Controls ---
    currency = models.CharField(
        _("Currency"),
        max_length=10, # Should match longest currency code (e.g., 'USD')
        choices=CurrencyType.choices,
        default=CurrencyType.USD.value, # Store the value ('USD')
        help_text=_("Primary currency for transactions posted to this account.")
    )
    is_active = models.BooleanField(
        _("Is Active"),
        default=True,
        db_index=True,
        help_text=_("Inactive accounts cannot be selected for new transactions.")
    )
    allow_direct_posting = models.BooleanField(
        _("Allow Direct Posting"),
        default=True,
        help_text=_("Can journal entries be posted directly to this account? (False for summary accounts).")
    )
    is_control_account = models.BooleanField(
        _("Is Control Account"),
        default=False,
        help_text=_("Mark True if this account summarizes a subsidiary ledger (e.g., Accounts Receivable).")
    )
    control_account_party_type = models.CharField(
        _("Control Account Party Type"),
        max_length=20, # Should match longest value in PartyType
        choices=PartyType.choices,
        null=True, blank=True, db_index=True,
        help_text=_("If Control Account, specify which Party Type it controls (e.g., CUSTOMER).")
    )

    # --- Ledger Balance Fields ---
    current_balance = models.DecimalField(
        _("Current Balance"),
        max_digits=20, decimal_places=2,
        default=Decimal('0.00'),
        editable=False,
        help_text=_("Calculated current balance based on posted transactions (updated asynchronously).")
    )
    balance_last_updated = models.DateTimeField(
        _("Balance Last Updated"),
        null=True, blank=True, editable=False,
        help_text=_("Timestamp when current_balance was last recalculated.")
    )

    # --- Audit Fields ---
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True, editable=False)

    class Meta:
        verbose_name = _('Account')
        verbose_name_plural = _('Accounts')
        ordering = ['account_group__name', 'account_number']
        indexes = [
            models.Index(fields=['account_type']),
            models.Index(fields=['pl_section']),
            models.Index(fields=['is_active', 'allow_direct_posting']),
            models.Index(fields=['is_control_account', 'control_account_party_type']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(is_control_account=False) | models.Q(control_account_party_type__isnull=False),
                name='control_account_requires_party_type',
                violation_error_message=_("Control accounts must specify a Control Account Party Type.")
            ),
            models.CheckConstraint(
                check=models.Q(is_control_account=True) | models.Q(control_account_party_type__isnull=True),
                name='party_type_requires_control_account',
                violation_error_message=_("Control Account Party Type can only be set on Control Accounts.")
            ),
        ]
        permissions = [
            ("view_financial_reports", "Can view financial reports"),
        ]

    def __str__(self):
        return f"{self.account_name} ({self.account_number})"

    def clean(self):
        """Custom model validation logic run before saving."""
        super().clean()
        # Validation for control account setup
        if self.is_control_account and not self.control_account_party_type:
             raise ValidationError({'control_account_party_type': _("Control accounts must specify a Control Account Party Type.")})
        if not self.is_control_account and self.control_account_party_type:
             raise ValidationError({'control_account_party_type': _("Cannot set Control Account Party Type on a non-control account.")})

        # --- Validate pl_section against account_type using VALUES ---
        # Check the value stored in self.account_type
        is_pl_type = self.account_type in [
            AccountType.INCOME.value,
            AccountType.EXPENSE.value,
            AccountType.COST_OF_GOODS_SOLD.value
        ]
        # Check the value stored in self.pl_section
        if is_pl_type and self.pl_section == PLSection.NONE.value:
            raise ValidationError({
                'pl_section': _("P&L Section must be set (cannot be 'NONE') for Income, Expense, or COGS account types.")
            })
        if not is_pl_type and self.pl_section != PLSection.NONE.value:
            raise ValidationError({
                'pl_section': _("P&L Section must be 'NONE' for Asset, Liability, or Equity account types.")
            })
        # --- End pl_section validation ---

        # --- Validate account_nature logic consistency ---
        # This ensures clean() catches mapping errors even before save() is called
        inferred_nature = ACCOUNT_TYPE_TO_NATURE.get(self.account_type)
        if not inferred_nature:
            # Raise error here during clean if the mapping is missing
             raise ValidationError({
                 'account_type': _("System configuration error: Cannot determine nature for account type '%(type)s'. Check ACCOUNT_TYPE_TO_NATURE mapping.") % {'type': self.account_type}
             })
        # Temporarily set nature for other potential clean checks (optional)
        # self.account_nature = inferred_nature


    def save(self, *args, **kwargs):
        """Overrides save to auto-set account nature and run full validation."""
        # 1. Auto-set nature from account type reliably before saving
        #    Uses the ACCOUNT_TYPE_TO_NATURE dictionary defined at the top of this file.
        #    Looks up based on the VALUE of self.account_type (e.g., 'ASSET', 'COGS').
        inferred_nature = ACCOUNT_TYPE_TO_NATURE.get(self.account_type)
        if inferred_nature:
            self.account_nature = inferred_nature
        else:
            # This should ideally be caught by clean(), but acts as a final safeguard.
            logger.critical(f"Account nature mapping missing for type {self.account_type} on account {self.account_number}!")
            raise ValidationError(_(f"System Error: Cannot save Account, missing nature mapping for type '{self.account_type}'."))

        # 2. Run full validation including clean() method and constraints
        #    Use exclude for fields calculated/set elsewhere (like by async tasks)
        self.full_clean(exclude=['current_balance', 'balance_last_updated'])

        # 3. Call original save
        super().save(*args, **kwargs)

    # --- Helper Properties & Methods ---
    @property
    def is_debit_nature(self) -> bool:
        """Helper property to check if the account naturally increases with debits."""
        return self.account_nature == AccountNature.DEBIT.value # Compare against value

    @property
    def is_credit_nature(self) -> bool:
        """Helper property to check if the account naturally increases with credits."""
        return self.account_nature == AccountNature.CREDIT.value # Compare against value

    def get_dynamic_balance(self, date_upto=None, start_date=None):
        """Dynamically calculates the balance or movement based on posted transactions."""
        # Import locally to avoid circular dependency
        from crp_accounting.models.journal import VoucherLine, TransactionStatus, DrCrType

        lines = VoucherLine.objects.filter(
            account=self,
            voucher__status=TransactionStatus.POSTED
        )
        date_filter = models.Q()
        if start_date: date_filter &= models.Q(voucher__date__gte=start_date)
        if date_upto: date_filter &= models.Q(voucher__date__lte=date_upto)
        lines = lines.filter(date_filter)

        aggregation = lines.aggregate(
            total_debit=models.functions.Coalesce(
                models.Sum('amount', filter=models.Q(dr_cr=DrCrType.DEBIT.value)), # Use .value
                Decimal('0.00'), output_field=models.DecimalField()
            ),
            total_credit=models.functions.Coalesce(
                models.Sum('amount', filter=models.Q(dr_cr=DrCrType.CREDIT.value)), # Use .value
                Decimal('0.00'), output_field=models.DecimalField()
            )
        )
        debit_total = aggregation['total_debit']
        credit_total = aggregation['total_credit']

        # Use helper properties which now compare values
        if self.is_debit_nature:
            balance = debit_total - credit_total
        elif self.is_credit_nature:
            balance = credit_total - debit_total
        else:
            logger.error(f"Account {self.account_number} has invalid nature '{self.account_nature}' during balance calculation.")
            balance = Decimal('0.00')
        return balance

    @classmethod
    def get_accounts_for_posting(cls):
        """Class method returns active accounts where direct posting is allowed."""
        return cls.objects.filter(is_active=True, allow_direct_posting=True)

#
# import logging
# from decimal import Decimal
# from django.db import models, transaction
# from django.utils.translation import gettext_lazy as _
# from django.core.exceptions import ValidationError
# from crp_core.enums import AccountType, AccountNature, CurrencyType, PartyType, DrCrType
#
# logger = logging.getLogger(__name__)
#
# # --- Constants ---
# # Mapping used to auto-set account nature. Ensure this is accessible.
# # It's often defined in a core constants file.
# ACCOUNT_TYPE_TO_NATURE = {
#     AccountType.ASSET.name: AccountNature.DEBIT.name,
#     AccountType.EXPENSE.name: AccountNature.DEBIT.name,
#     AccountType.LIABILITY.name: AccountNature.CREDIT.name,
#     AccountType.INCOME.name: AccountNature.CREDIT.name,
#     AccountType.EQUITY.name: AccountNature.CREDIT.name,
# }
#
#
# class AccountGroup(models.Model):
#     """
#     Represents a hierarchical grouping for the Chart of Accounts (COA).
#
#     Similar to Tally Groups, this allows structuring accounts into logical
#     categories (e.g., Assets -> Current Assets -> Bank Accounts).
#     It facilitates reporting aggregation and COA organization.
#     """
#     name = models.CharField(
#         _("Group Name"),
#         max_length=150,
#         unique=True,
#         db_index=True,
#         help_text=_("Unique name for the account group (e.g., Current Assets, Operating Expenses).")
#     )
#     is_primary = models.BooleanField(default=False)
#     description = models.TextField(
#         _("Description"),
#         blank=True,
#         help_text=_("Optional description of the account group's purpose.")
#     )
#     parent_group = models.ForeignKey(
#         'self',
#         verbose_name=_("Parent Group"),
#         on_delete=models.PROTECT,  # Prevent deleting a group if it has sub-groups
#         null=True,
#         blank=True,
#         related_name='sub_groups',
#         help_text=_("Assign a parent group to create a hierarchy (e.g., 'Current Assets' is under 'Assets'). Leave blank for top-level groups.")
#     )
#     is_primary = models.BooleanField(
#         _("Is Primary Group"),
#         default=False,
#         help_text=_("Mark as True if this is a top-level group like Assets, Liabilities, Equity, Income, or Expenses.")
#     )
#
#     # Standard audit fields
#     created_at = models.DateTimeField(_("Created At"), auto_now_add=True, editable=False)
#     updated_at = models.DateTimeField(_("Updated At"), auto_now=True, editable=False)
#
#     class Meta:
#         verbose_name = _('Account Group')
#         verbose_name_plural = _('Account Groups')
#         ordering = ['name']  # Default ordering
#
#     def __str__(self):
#         """String representation showing the group name."""
#         return self.name
#
#     def get_all_child_accounts(self):
#         """Recursively gets all accounts under this group and its sub-groups."""
#         accounts = list(self.accounts.all())
#         for sub_group in self.sub_groups.all():
#             accounts.extend(sub_group.get_all_child_accounts())
#         return accounts
#
#
# class Account(models.Model):
#     """
#     Represents a specific ledger account within the Chart of Accounts (COA).
#
#     This is the level where transactions are typically posted (unless direct posting is disallowed).
#     It defines the account's classification, nature, and links it to its group.
#     Balances are calculated dynamically from associated JournalLine entries.
#     """
#
#     account_number = models.CharField(
#         _("Account Number/Code"),
#         max_length=50,
#         unique=True,
#         db_index=True,
#         help_text=_("Unique identifier code for the account (e.g., 10100, 40001).")
#     )
#     account_name = models.CharField(
#         _("Account Name"),
#         max_length=255,
#         db_index=True,
#         help_text=_("Human-readable name of the account (e.g., Cash On Hand, Sales Revenue - Services).")
#     )
#     description = models.TextField(
#         _("Description"),
#         blank=True,
#         help_text=_("Optional detailed description of the account's purpose or usage.")
#     )
#     account_group = models.ForeignKey(
#         AccountGroup,
#         verbose_name=_("Account Group"),
#         on_delete=models.PROTECT, # Prevent deleting group if accounts exist under it
#         related_name='accounts',
#         help_text=_("The hierarchical group this account belongs to (e.g., 'Cash' belongs to 'Bank Accounts' group).")
#     )
#     account_type = models.CharField(
#         _("Account Type"),
#         max_length=20,
#         choices=AccountType.choices,
#         help_text=_("Fundamental accounting classification (Asset, Liability, Income, Expense, Equity). Determines the account's role in financial statements.")
#     )
#     account_nature = models.CharField(
#         _("Account Nature"),
#         max_length=10,
#         choices=AccountNature.choices,
#         editable=False, # Automatically set based on Account Type
#         help_text=_("System-inferred Dr/Cr nature (Debit/Credit). Based on the Account Type.")
#     )
#     currency = models.CharField(
#         _("Currency"),
#         max_length=10,
#         choices=CurrencyType.choices,
#         default=CurrencyType.USD.name, # Set your system's default currency
#         help_text=_("The primary currency for transactions posted to this account.")
#     )
#     is_active = models.BooleanField(
#         _("Is Active"),
#         default=True,
#         db_index=True,
#         help_text=_("Inactive accounts cannot be selected for new transactions.")
#     )
#     allow_direct_posting = models.BooleanField(
#         _("Allow Direct Posting"),
#         default=True,
#         help_text=_("Can journal entries be posted directly to this account? Set to False for summary or group-level accounts where posting should only happen to sub-accounts.")
#     )
#     is_control_account = models.BooleanField(
#         _("Is Control Account"),
#         default=False,
#         help_text=_("Mark True if this account summarizes a subsidiary ledger (e.g., Accounts Receivable controls the Customer ledger, Accounts Payable controls the Supplier ledger).")
#     )
#     control_account_party_type = models.CharField(
#         _("Control Account Party Type"),
#         max_length=20,
#         choices=PartyType.choices,
#         null=True, blank=True,
#         help_text=_("If 'Is Control Account' is True, specify which Party Type this account controls (e.g., CUSTOMER for Accounts Receivable).")
#     )
#
#     # Standard audit fields
#     created_at = models.DateTimeField(_("Created At"), auto_now_add=True, editable=False)
#     updated_at = models.DateTimeField(_("Updated At"), auto_now=True, editable=False)
#
#     class Meta:
#         verbose_name = _('Account')
#         verbose_name_plural = _('Accounts')
#         ordering = ['account_group__name', 'account_number'] # Order logically by group then number
#         constraints = [
#             models.CheckConstraint(
#                 check=models.Q(is_control_account=False) | models.Q(control_account_party_type__isnull=False),
#                 name='control_account_requires_party_type',
#                 violation_error_message=_("Control accounts must specify a Control Account Party Type.")
#             ),
#             models.CheckConstraint(
#                 check=models.Q(is_control_account=True) | models.Q(control_account_party_type__isnull=True),
#                 name='party_type_requires_control_account',
#                 violation_error_message=_("Control Account Party Type can only be set on Control Accounts.")
#             )
#         ]
#
#     def __str__(self):
#         """String representation including name and number."""
#         return f"{self.account_name} ({self.account_number})"
#
#     def clean(self):
#         """Custom validation logic run before saving."""
#         super().clean()
#         # Ensure control account setup is valid
#         if self.is_control_account and not self.control_account_party_type:
#              # This is also covered by constraints, but good practice to have in clean()
#              raise ValidationError(_("Control accounts must specify a Control Account Party Type."))
#         if not self.is_control_account and self.control_account_party_type:
#              raise ValidationError(_("Cannot set Control Account Party Type on a non-control account."))
#
#         # Prevent direct posting to inactive accounts if desired (though usually handled in transaction forms)
#         # if not self.is_active and self.allow_direct_posting:
#         #     raise ValidationError(_("Inactive accounts cannot allow direct posting."))
#
#     def save(self, *args, **kwargs):
#         """
#         Overrides save to auto-set account nature before saving.
#         """
#         # 1. Auto-set nature from account type
#         inferred_nature = ACCOUNT_TYPE_TO_NATURE.get(self.account_type)
#         if inferred_nature:
#             self.account_nature = inferred_nature
#         else:
#             # This indicates a setup issue (missing mapping in ACCOUNT_TYPE_TO_NATURE)
#             logger.error(f"Could not determine account nature for type {self.account_type} on account {self.account_number}. Check ACCOUNT_TYPE_TO_NATURE mapping.")
#             # Depending on strictness, you might raise ValidationError here
#             # raise ValidationError(_(f"System configuration error: Cannot determine nature for account type '{self.account_type}'."))
#             # Or default to a safe value if appropriate (less recommended)
#             # self.account_nature = AccountNature.DEBIT.name # Example default - use with caution
#
#         # 2. Run full validation
#         self.full_clean() # Ensures `clean()` and field validations run
#
#         # 3. Call original save
#         super().save(*args, **kwargs)
#
#     def get_balance(self, date_upto=None, start_date=None):
#         """
#         Dynamically calculates the balance or movement of the account.
#
#         - If only `date_upto` is provided, calculates the closing balance as of that date.
#         - If `start_date` and `date_upto` are provided, calculates the net movement
#           within that period (useful for P&L accounts).
#         - If neither is provided, calculates the lifetime balance.
#
#         Args:
#             date_upto (date, optional): Calculate balance up to this date (inclusive).
#             start_date (date, optional): Calculate movement starting from this date (inclusive).
#
#         Returns:
#             Decimal: The calculated balance or movement.
#         """
#         # Import locally to avoid circular dependency issues at module load time
#         from crp_accounting.models.journal import VoucherLine
#
#         lines = VoucherLine.objects.filter(account=self)
#
#         # Apply date filters
#         if start_date:
#             lines = lines.filter(journal_entry__date__gte=start_date)
#         if date_upto:
#             lines = lines.filter(journal_entry__date__lte=date_upto)
#
#         # Aggregate debits and credits within the filtered range
#         aggregation = lines.aggregate(
#             total_debit=models.Sum(
#                 models.Case(
#                     models.When(dr_cr=DrCrType.DEBIT.name, then='amount'),
#                     default=Decimal('0.00'),
#                     output_field=models.DecimalField()
#                 )
#             ),
#             total_credit=models.Sum(
#                 models.Case(
#                     models.When(dr_cr=DrCrType.CREDIT.name, then='amount'),
#                     default=Decimal('0.00'),
#                     output_field=models.DecimalField()
#                 )
#             )
#         )
#
#         debit_total = aggregation.get('total_debit') or Decimal('0.00')
#         credit_total = aggregation.get('total_credit') or Decimal('0.00')
#
#         # Determine balance based on account nature
#         # For closing balance (no start_date or start_date is very early)
#         # For period movement (start_date is provided) - net change is typically Dr - Cr
#         if self.account_nature == AccountNature.DEBIT.name:
#             balance = debit_total - credit_total
#         elif self.account_nature == AccountNature.CREDIT.name:
#             balance = credit_total - debit_total
#         else:
#             # Should not happen if nature is always set
#             logger.warning(f"Account {self.account_number} has undefined nature '{self.account_nature}'. Returning raw Dr-Cr.")
#             balance = debit_total - credit_total
#
#         return balance
#
#     def is_debit_nature(self):
#         """Helper method to check if the account has a debit nature."""
#         return self.account_nature == AccountNature.DEBIT.name
#
#     def is_credit_nature(self):
#         """Helper method to check if the account has a credit nature."""
#         return self.account_nature == AccountNature.CREDIT.name
#
#     @classmethod
#     def get_accounts_for_posting(cls):
#         """Returns active accounts where direct posting is allowed."""
#         return cls.objects.filter(is_active=True, allow_direct_posting=True)