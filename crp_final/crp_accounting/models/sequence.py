from django.db import models
from django.utils.translation import gettext_lazy as _
from .period import AccountingPeriod # Import your AccountingPeriod model
from .journal import VoucherType # Assuming VoucherType enum/choices are defined here or imported

class VoucherSequence(models.Model):
    """
    Manages the next available voucher number for a specific scope.
    Scope is typically defined by Journal Type and Accounting Period.
    """
    journal_type = models.CharField(
        max_length=20,
        choices=VoucherType.choices,
        help_text=_("The type of journal this sequence is for.")
    )
    accounting_period = models.ForeignKey(
        AccountingPeriod,
        on_delete=models.CASCADE, # If period deleted, sequence makes no sense
        help_text=_("The accounting period this sequence applies to.")
    )
    prefix = models.CharField(
        max_length=10,
        blank=True,
        help_text=_("Prefix for the voucher number (e.g., 'JV-').")
    )
    padding_digits = models.PositiveSmallIntegerField(
        default=4,
        help_text=_("Number of digits for padding (e.g., 4 means 0001, 0010, 0100).")
    )
    last_number = models.PositiveIntegerField(
        default=0,
        help_text=_("The last number used in this sequence.")
    )

    class Meta:
        verbose_name = _("Voucher Sequence")
        verbose_name_plural = _("Voucher Sequences")
        # Ensure only one sequence exists per type/period combination
        unique_together = ('journal_type', 'accounting_period')
        ordering = ['accounting_period', 'journal_type']

    def __str__(self):
        return f"{self.get_journal_type_display()} sequence for {self.accounting_period}"

    def get_next_formatted_number(self, current_number: int) -> str:
        """Formats the next number according to prefix and padding."""
        number_str = str(current_number).zfill(self.padding_digits)
        return f"{self.prefix}{number_str}"