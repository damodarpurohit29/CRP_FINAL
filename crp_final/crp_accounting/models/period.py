from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class FiscalYear(models.Model):
    """
    Represents a fiscal (financial) year for the organization.
    Used to segregate accounting data and control period boundaries.
    """

    name = models.CharField(max_length=100, unique=True, help_text=_("Label for the fiscal year (e.g., 2024-2025)"))
    start_date = models.DateField(help_text=_("Start date of the fiscal year."))
    end_date = models.DateField(help_text=_("End date of the fiscal year."))
    is_active = models.BooleanField(default=False, help_text=_("Only one fiscal year can be active at a time."))
    status = models.CharField(
        max_length=20,
        choices=[("Open", "Open"), ("Locked", "Locked"), ("Closed", "Closed")],
        default="Open",
        help_text=_("Operational status of the fiscal year.")
    )
    closed_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL,
        help_text=_("User who closed the year.")
    )
    closed_at = models.DateTimeField(null=True, blank=True, help_text=_("Timestamp when the fiscal year was closed."))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = _('Fiscal Year')
        verbose_name_plural = _('Fiscal Years')

    def __str__(self):
        return self.name

    def clean(self):
        if self.end_date <= self.start_date:
            raise ValidationError(_("End date must be after the start date."))
        if self.is_active:
            # Ensure only one active year
            if FiscalYear.objects.exclude(pk=self.pk).filter(is_active=True).exists():
                raise ValidationError(_("Another fiscal year is already active."))

    def activate(self):
        """Activate this year and deactivate all others."""
        FiscalYear.objects.exclude(pk=self.pk).update(is_active=False)
        self.is_active = True
        self.status = "Open"
        self.save()

    def close_year(self, user=None):
        """Closes the year, locking further transactions."""
        self.status = "Closed"
        self.closed_by = user
        self.closed_at = timezone.now()
        self.save()



class AccountingPeriod(models.Model):
    """
    Model to represent an accounting period within a fiscal year.
    This allows for locking a period to prevent any further transactions
    after it has been closed.

    Attributes:
        - `start_date`: The start date of the accounting period.
        - `end_date`: The end date of the accounting period.
        - `fiscal_year`: The fiscal year this period belongs to.
        - `locked`: Boolean flag to indicate if this period is closed and no more entries are allowed.
    """


    start_date = models.DateField(help_text=_("The start date of the accounting period."))
    end_date = models.DateField(help_text=_("The end date of the accounting period."))
    fiscal_year = models.ForeignKey(
        'FiscalYear', on_delete=models.CASCADE, related_name="periods",
        help_text=_("The fiscal year to which this period belongs.")
    )
    locked = models.BooleanField(default=False, help_text=_("Indicates whether the period is locked and no more entries are allowed."))

    def __str__(self):
        """
        String representation of the AccountingPeriod model.
        Returns a string indicating the period's start and end date.
        """
        return f"Period {self.start_date} to {self.end_date} ({'Locked' if self.locked else 'Open'})"

    def lock_period(self):
        """
        Locks the accounting period to prevent further journal entries.
        """
        if self.locked:
            raise ValidationError(_("This period is already locked."))
        self.locked = True
        self.save()

    def unlock_period(self):
        """
        Unlocks the accounting period to allow further journal entries.
        """
        if not self.locked:
            raise ValidationError(_("This period is already open."))
        self.locked = False
        self.save()

    class Meta:
        """
        Meta options for the AccountingPeriod model.
        """
        verbose_name = _('Accounting Period')
        verbose_name_plural = _('Accounting Periods')
