from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html # For custom display methods
from django.urls import reverse # For links
from ..models.journal import (
    Voucher, VoucherLine, VoucherSequence, VoucherApproval,
    TransactionStatus # Import status enum for checks
)


# =============================================================================
# Inline Admin for Voucher Lines
# =============================================================================

class VoucherLineInline(admin.TabularInline):
    """
    Inline admin configuration for Voucher Lines displayed within the Voucher admin.
    Allows adding/editing lines directly when viewing a Voucher.
    """
    model = VoucherLine
    fields = ('account', 'dr_cr', 'amount', 'narration') # Fields shown in the inline form
    extra = 1 # Show 1 extra blank line for adding new lines
    # Use autocomplete for account selection if you have many accounts (requires setup in AccountAdmin)
    autocomplete_fields = ['account']
    # Define classes for styling if needed
    classes = ['collapse'] # Start collapsed by default if desired

    def get_readonly_fields(self, request, obj=None):
        """Make lines read-only if the parent voucher is posted/cancelled."""
        readonly = super().get_readonly_fields(request, obj)
        # obj here is the VoucherLine instance if editing an existing line,
        # but often we need the parent Voucher (obj passed to VoucherAdmin)
        # A common way is to access the parent through a form field if available,
        # or make assumptions if obj is None (during add). Simpler check:
        # We'll rely on VoucherAdmin's overall read-only state for simplicity here,
        # but more complex logic checking obj.voucher.status could be added if needed.
        return readonly

    def has_add_permission(self, request, obj=None):
        """Prevent adding lines if the parent voucher is posted/cancelled."""
        # obj here is the PARENT Voucher instance passed from VoucherAdmin
        if obj and obj.status in [TransactionStatus.POSTED, TransactionStatus.CANCELLED]:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting lines if the parent voucher is posted/cancelled."""
        # obj here is the PARENT Voucher instance passed from VoucherAdmin
        if obj and obj.status in [TransactionStatus.POSTED, TransactionStatus.CANCELLED]:
            return False
        return super().has_delete_permission(request, obj)


# =============================================================================
# Main Voucher Admin
# =============================================================================

@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Voucher model.
    Provides list display, filtering, search, inline editing of lines,
    and conditional read-only fields based on status.
    """
    list_display = (
        'id',  # <--- Add this
        'voucher_number_link', # Use custom method for link
        'date',
        'voucher_type',
        'status',
        'party_link', # Use custom method for link
        'narration_short', # Custom method for truncated narration
        'total_debit',
        'total_credit',
        'is_balanced_display', # Custom method for icon/text
        'accounting_period',
        'updated_at',
    )
    list_filter = (
        'status',
        'voucher_type',
        'accounting_period',
        ('date', admin.DateFieldListFilter), # Use date filter
        'party',
    )
    search_fields = (
        'voucher_number',
        'reference',
        'narration',
        'party__name', # Search related Party name
        'lines__narration', # Search line narrations
        'lines__account__account_name', # Search line account names
        'lines__account__account_code', # Search line account codes
    )
    date_hierarchy = 'date' # Allows quick date drill-down
    ordering = ('-date', '-voucher_number') # Default ordering in admin list
    inlines = [VoucherLineInline] # Embed the line editor
    # Use autocomplete for foreign keys with many options
    autocomplete_fields = ['party', 'accounting_period'] # Requires setup in PartyAdmin/PeriodAdmin

    # Define fieldsets for the detail/change view for better organization
    fieldsets = (
        (None, { # Main section (no title)
            'fields': (('date', 'effective_date'), 'voucher_number', 'voucher_type', 'status')
        }),
        (_('Details'), {
            'fields': ('reference', 'narration')
        }),
        (_('Association'), {
            'fields': ('party', 'accounting_period', ('content_type', 'object_id'))
        }),
        (_('System Info'), {
            'classes': ('collapse',), # Collapse this section by default
            'fields': ('balances_updated', 'created_at', 'updated_at'),
        }),
        # Note: Lines are handled by the inline, not listed in fieldsets
    )

    def get_readonly_fields(self, request, obj=None):
        """
        Determine read-only fields based on voucher status.
        Posted or Cancelled vouchers should largely be read-only.
        """
        readonly = ['voucher_number', 'created_at', 'updated_at', 'balances_updated'] # Always read-only
        if obj: # If viewing/editing an existing voucher
            if obj.status in [TransactionStatus.POSTED, TransactionStatus.CANCELLED]:
                # Make most fields read-only for posted/cancelled vouchers
                readonly.extend([
                    'date', 'effective_date', 'reference', 'narration', 'voucher_type',
                    'status', 'party', 'accounting_period', 'content_type', 'object_id'
                ])
        # Allow setting status only on create? For admin, might allow changing DRAFT/REJECTED
        # For now, status is read-only if posted/cancelled via the check above.
        return readonly

    # --- Custom display methods for list_display ---

    @admin.display(description=_('Voucher No.'), ordering='voucher_number')
    def voucher_number_link(self, obj):
        """Make voucher number a link to the change view."""
        if obj.voucher_number:
            url = reverse('admin:crp_accounting_voucher_change', args=[obj.pk])
            return format_html('<a href="{}">{}</a>', url, obj.voucher_number)
        return _('(Not Assigned)')

    @admin.display(description=_('Party'), ordering='party__name')
    def party_link(self, obj):
        """Make party a link to the Party change view (if applicable)."""
        if obj.party:
            # Assuming you have a PartyAdmin registered under app 'crp_core' or similar
            try:
                url = reverse('admin:crp_core_party_change', args=[obj.party.pk]) # Adjust app_label if needed
                return format_html('<a href="{}">{}</a>', url, obj.party)
            except Exception: # Catch NoReverseMatch if Party admin not setup
                return str(obj.party)
        return '-'

    @admin.display(description=_('Narration'))
    def narration_short(self, obj):
        """Display a shortened version of the narration."""
        from django.utils.text import Truncator
        return Truncator(obj.narration).chars(50, truncate='...')

    @admin.display(description=_('Balanced?'), boolean=True)
    def is_balanced_display(self, obj):
        """Display a boolean icon for the is_balanced property."""
        return obj.is_balanced

    # Optional: Add admin actions to perform workflow steps (use with caution!)
    # actions = ['submit_selected_vouchers', 'approve_selected_vouchers']
    #
    # def submit_selected_vouchers(self, request, queryset):
    #     # ... Call voucher_workflow.submit_voucher_for_approval for each valid voucher ...
    #     # ... Add messages for success/failure ...
    # submit_selected_vouchers.short_description = _("Submit selected Draft Vouchers for Approval")
    #
    # def approve_selected_vouchers(self, request, queryset):
    #     # ... Call voucher_workflow.approve_and_post_voucher for each valid voucher ...
    #     # ... Add messages for success/failure ...
    # approve_selected_vouchers.short_description = _("Approve and Post selected Vouchers")


# =============================================================================
# Voucher Sequence Admin
# =============================================================================

@admin.register(VoucherSequence)
class VoucherSequenceAdmin(admin.ModelAdmin):
    """Admin configuration for managing Voucher Sequences."""
    list_display = ('voucher_type', 'accounting_period', 'prefix', 'padding_digits', 'last_number', 'updated_at')
    list_filter = ('voucher_type', 'accounting_period')
    search_fields = ('prefix', 'accounting_period__name')
    list_editable = ('prefix', 'padding_digits') # Allow quick edits in list view
    list_per_page = 25
    ordering = ('accounting_period__start_date', 'voucher_type')
    # Make last_number read-only to prevent accidental manual changes? Or editable for corrections?
    # readonly_fields = ['last_number'] # Uncomment if desired


# =============================================================================
# Voucher Approval Log Admin
# =============================================================================

@admin.register(VoucherApproval)
class VoucherApprovalAdmin(admin.ModelAdmin):
    """Admin configuration for viewing Voucher Approval logs (read-only)."""
    list_display = ('voucher_link', 'user', 'action_type', 'action_timestamp', 'from_status', 'to_status', 'comments_short')
    list_filter = ('action_type', 'user', ('action_timestamp', admin.DateFieldListFilter))
    search_fields = ('voucher__voucher_number', 'user__username', 'comments')
    list_per_page = 50
    ordering = ('-action_timestamp',)
    autocomplete_fields = ['voucher', 'user']

    def get_readonly_fields(self, request, obj=None):
        """Make all fields read-only as this is a log."""
        # Dynamically get all fields from the model
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        """Prevent adding logs manually via admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent changing logs manually via admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting logs manually via admin."""
        return False

    # --- Custom display methods ---
    @admin.display(description=_('Voucher'), ordering='voucher__voucher_number')
    def voucher_link(self, obj):
        """Link to the related voucher."""
        if obj.voucher:
            url = reverse('admin:crp_accounting_voucher_change', args=[obj.voucher.pk])
            return format_html('<a href="{}">{}</a>', url, obj.voucher.voucher_number or f"Voucher #{obj.voucher.pk}")
        return '-'

    @admin.display(description=_('Comments'))
    def comments_short(self, obj):
        """Display shortened comments."""
        from django.utils.text import Truncator
        return Truncator(obj.comments).chars(50, truncate='...')

# from django.contrib import admin
# from django.core.exceptions import ValidationError
# from django.utils.translation import gettext_lazy as _
# from django.forms import BaseInlineFormSet
#
# from crp_accounting.models.journal import JournalLine, JournalEntry
#
#
# class JournalLineInline(admin.TabularInline):
#     """
#     Inline admin interface to allow editing journal lines within a Journal Entry.
#     Enforces double-entry logic visually (must manually validate when saving).
#     """
#     model = JournalLine
#     extra = 2
#     fields = ('account', 'dr_cr', 'amount', 'narration')
#     show_change_link = False
#
#
# @admin.register(JournalEntry)
# class JournalEntryAdmin(admin.ModelAdmin):
#     """
#     Admin interface for journal entries with inline journal lines.
#     Enforces double-entry and checks for locked accounting periods.
#     """
#     list_display = ('id', 'date', 'journal_type', 'status', 'party', 'accounting_period')
#     list_filter = ('journal_type', 'status', 'date', 'accounting_period__locked')
#     search_fields = ('narration', 'reference', 'party__name')
#
#     inlines = [JournalLineInline]
#     readonly_fields = ('created_at', 'updated_at')
#
#     def save_model(self, request, obj, form, change):
#         """
#         Enforce business rules on save: balanced entries and unlocked period.
#         """
#         # Check accounting period is not locked
#         if obj.accounting_period and obj.accounting_period.locked:
#             raise ValidationError(
#                 _("Cannot save this journal entry because the selected accounting period is locked.")
#             )
#
#         super().save_model(request, obj, form, change)
#
#         # Validate that debits = credits after lines are saved
#         debit_total = 0
#         credit_total = 0
#
#         for line in obj.lines.all():
#             if line.dr_cr == 'DEBIT':
#                 debit_total += line.amount
#             elif line.dr_cr == 'CREDIT':
#                 credit_total += line.amount
#
#         if debit_total != credit_total:
#             raise ValidationError(
#                 _("Journal entry is unbalanced: Debits = {debit_total}, Credits = {credit_total}").format(
#                     debit_total=debit_total,
#                     credit_total=credit_total
#                 )
#             )
