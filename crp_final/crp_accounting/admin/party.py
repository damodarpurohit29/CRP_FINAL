# crp_accounting/admin.py

from django.contrib import admin
from django.db import models # For potential filtering if needed
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from decimal import Decimal, InvalidOperation

from crp_accounting.models import Party, Account
# --- Model and Enum Imports ---
# Make sure these imports are correct based on your project structure
 # Need Account model for filtering queryset
from crp_core.enums import PartyType # Need PartyType enum for filtering

# Optional: If you want to filter by date ranges
from django.contrib.admin import DateFieldListFilter

@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Party model.

    Provides list display, filtering, search, field organization,
    and actions for managing customers, suppliers, etc.

    Includes dynamic balance calculation for display purposes (can be slow).
    Filters Control Account choices based on Party Type.
    """
    list_display = (
        'name',
        'party_type',
        'control_account_link', # Display linked control account
        'is_active',
        'contact_phone',
        'credit_limit',
        'display_calculated_balance', # Method to show dynamic balance
        'get_credit_status_display', # Method to show credit status
    )
    list_filter = (
        'party_type',
        'is_active',
        ('control_account', admin.RelatedOnlyFieldListFilter), # Filter by assigned control account
        ('created_at', DateFieldListFilter),
    )
    search_fields = (
        'name',
        'contact_email',
        'contact_phone',
        'control_account__account_name', # Search by linked account name
        'control_account__account_number', # Search by linked account number
    )
    list_select_related = ('control_account',) # Pre-fetch control account for display
    readonly_fields = (
        'created_at',
        'updated_at',
        # 'display_balance_in_form', # Only uncomment if you add the method below
    )
    fieldsets = (
        (None, {
            'fields': ('party_type', 'name', 'is_active')
        }),
        ('Contact Information', {
            'fields': ('contact_email', 'contact_phone', 'address')
        }),
        ('Accounting & Credit Settings', {
            # Ensure 'control_account' is here
            'fields': ('control_account', 'credit_limit')
        }),
        ('Audit Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    autocomplete_fields = ['control_account'] # Good choice for potentially long account lists
    list_per_page = 25

    # --- ADDED: Method to filter Control Account choices ---
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Filters the choices for the 'control_account' field based on Party Type
        when editing an existing Party. Shows relevant control accounts otherwise.
        """
        if db_field.name == "control_account":
            # Try to get the party type of the object being edited
            object_id = request.resolver_match.kwargs.get('object_id')
            party_type = None
            instance = None
            if object_id:
                try:
                    # Use get_queryset to respect admin filters if any future customization
                    instance = self.get_queryset(request).filter(pk=object_id).first()
                    if instance:
                         party_type = instance.party_type
                except Exception as e:
                    # Log error or handle gracefully if needed
                    print(f"Warning: Could not get Party instance {object_id} for filtering: {e}")
                    pass # Fallback to default filtering

            # Apply filtering based on the determined party_type
            if party_type == PartyType.CUSTOMER.value:
                # Show only Customer control accounts if editing a Customer
                kwargs["queryset"] = Account.objects.filter(
                    is_control_account=True,
                    control_account_party_type=PartyType.CUSTOMER.value,
                    is_active=True # Also ensure the account is active
                ).order_by('account_number')
                self.message_user(request, "Showing Accounts Receivable control accounts.", level='info')
            elif party_type == PartyType.SUPPLIER.value:
                # Show only Supplier control accounts if editing a Supplier
                kwargs["queryset"] = Account.objects.filter(
                    is_control_account=True,
                    control_account_party_type=PartyType.SUPPLIER.value,
                    is_active=True
                ).order_by('account_number')
                self.message_user(request, "Showing Accounts Payable control accounts.", level='info')
            else:
                # When adding new OR if type is not Customer/Supplier
                # Show all active Customer & Supplier control accounts by default
                kwargs["queryset"] = Account.objects.filter(
                    is_control_account=True,
                    control_account_party_type__in=[
                        PartyType.CUSTOMER.value,
                        PartyType.SUPPLIER.value
                    ],
                    is_active=True
                ).order_by('account_number')
                # Optionally add a message if adding new
                if not object_id:
                     self.message_user(request, "Select the appropriate Control Account (Accounts Receivable for Customer, Accounts Payable for Supplier).", level='warning')

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    # --- END ADDED METHOD ---

    # --- Custom Methods for Display (Your existing methods) ---
    @admin.display(description='Control Account', ordering='control_account__account_name')
    def control_account_link(self, obj):
        """Displays the control account name as a link if it exists."""
        if obj.control_account:
            return str(obj.control_account)
        return "N/A" # Changed from '-' for clarity

    @admin.display(description='Current Balance', ordering=None)
    def display_calculated_balance(self, obj):
        """Calculates and displays the current outstanding balance."""
        # PERFORMANCE WARNING: This calculation runs for every row in list view.
        if not obj.control_account:
             return "N/A (No Control Acct)" # Cannot calculate without control account
        try:
            # Assuming Party model has this method:
            balance = obj.calculate_outstanding_balance()
            currency_symbol = '₹' if obj.control_account.currency == 'INR' else '$' # Basic currency symbol
            balance_str = f"{currency_symbol}{balance:,.2f}"
            # Basic coloring
            color = "red" if balance < 0 else "black"
            # More advanced coloring based on nature (requires control_account)
            # if obj.control_account:
            #     if balance < 0 and obj.control_account.is_debit_nature(): color = "red" # e.g. Negative AR balance
            #     elif balance < 0 and obj.control_account.is_credit_nature(): color = "green" # e.g. Negative AP balance (means you overpaid)
            #     elif balance > 0 and obj.control_account.is_debit_nature(): color = "black" # Normal AR
            #     elif balance > 0 and obj.control_account.is_credit_nature(): color = "black" # Normal AP

            return format_html('<span style="color: {};">{}</span>', color, balance_str)
        except AttributeError:
            return "N/A (Method Missing?)" # If calculate_outstanding_balance doesn't exist
        except Exception as e:
             # Log the actual error for debugging
             print(f"Error calculating balance for Party {obj.pk}: {e}")
             return "Calculation Error"


    @admin.display(description='Credit Status')
    def get_credit_status_display(self, obj):
        """Displays the credit status based on the model method."""
        # Assuming Party model has this method:
        try:
            status = obj.get_credit_status()
            color = "red" if status == "Over Credit Limit" else "green" if status == "Within Limit" else "grey"
            return format_html('<span style="color: {};">{}</span>', color, status)
        except AttributeError:
            return "N/A (Method Missing?)"
        except Exception as e:
             print(f"Error getting credit status for Party {obj.pk}: {e}")
             return "Error"


    # --- Admin Actions (Your existing actions) ---
    actions = ['make_active', 'make_inactive']

    @admin.action(description='Mark selected parties as active')
    def make_active(self, request, queryset):
        updated_count = queryset.update(is_active=True)
        self.message_user(request, f"Marked {updated_count} parties as active.")

    @admin.action(description='Mark selected parties as inactive')
    def make_inactive(self, request, queryset):
        active_parties = queryset.filter(is_active=True)
        updated_count = 0
        excluded_count = 0
        for party in active_parties:
            try:
                 # Recalculate balance before deactivating
                 if hasattr(party, 'calculate_outstanding_balance') and party.control_account and party.calculate_outstanding_balance() != Decimal('0.00'):
                     self.message_user(request, f"Cannot deactivate party '{party.name}' due to non-zero balance.", level='warning')
                     excluded_count += 1
                 else:
                     party.is_active = False
                     party.save(update_fields=['is_active']) # Save individually after check
                     updated_count += 1
            except Exception as e:
                 self.message_user(request, f"Error checking/deactivating party '{party.name}': {e}", level='error')
                 excluded_count += 1

        if updated_count > 0:
             self.message_user(request, f"Marked {updated_count} parties as inactive.")
        if excluded_count > 0:
             self.message_user(request, f"Could not deactivate {excluded_count} parties (check warnings/errors).", level='warning')