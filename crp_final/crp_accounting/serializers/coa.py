# crp_accounting/serializers.py (or coa/serializers.py)

import logging
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from decimal import Decimal

# --- Model Imports ---
# Adjust path based on where this serializers.py file lives relative to models
from ..models.coa import Account, AccountGroup # Import from coa.py

# --- Enum Imports ---
# Assuming enums are centrally located or accessible
from crp_core.enums import AccountType, AccountNature, CurrencyType, PartyType, DrCrType

logger = logging.getLogger(__name__)

# =============================================================================
# Helper/Summary Serializers
# =============================================================================

class AccountGroupSummarySerializer(serializers.ModelSerializer):
    """Minimal representation of AccountGroup for nesting."""
    class Meta:
        model = AccountGroup
        fields = ['id', 'name'] # Removed is_primary


class AccountSummarySerializer(serializers.ModelSerializer):
    """Minimal representation of Account for nesting or summaries."""
    class Meta:
        model = Account
        # Include fields useful for display in lists/dropdowns
        fields = ['id', 'account_number', 'account_name', 'is_active', 'account_type']


# =============================================================================
# AccountGroup Serializers (Updated)
# =============================================================================

class AccountGroupReadSerializer(serializers.ModelSerializer):
    """Serializer for *reading* AccountGroup data."""
    parent_group = AccountGroupSummarySerializer(read_only=True)

    class Meta:
        model = AccountGroup
        fields = [
            'id',
            'name',
            'description',
            'parent_group',
            # 'is_primary', # Removed
            'created_at',
            'updated_at',
            # Optionally add sub_groups or accounts if needed for specific endpoints
            # 'sub_groups': AccountGroupSummarySerializer(many=True, read_only=True),
            # 'accounts': AccountSummarySerializer(many=True, read_only=True),
        ]
        read_only_fields = fields


class AccountGroupWriteSerializer(serializers.ModelSerializer):
    """Serializer for *creating/updating* AccountGroup data."""
    parent_group = serializers.PrimaryKeyRelatedField(
        queryset=AccountGroup.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = AccountGroup
        fields = [
            'id',
            'name',
            'description',
            'parent_group',
            # 'is_primary', # Removed
        ]
        read_only_fields = ('id',)

    def validate_parent_group(self, value):
        """Prevent circular dependencies."""
        if value is None: return value
        instance = getattr(self, 'instance', None)
        if instance and instance == value:
            raise serializers.ValidationError(_("An account group cannot be its own parent."))
        # Check ancestors
        parent = value
        visited = {value.id} # Keep track of visited parents to detect loops quickly
        while parent is not None:
            if instance and parent == instance:
                 raise serializers.ValidationError(_("Circular dependency detected. Cannot set parent group."))
            parent = parent.parent_group
            if parent and parent.id in visited: # More efficient loop check
                 logger.error(f"Circular parent group dependency detected involving IDs {visited} and {parent.id}")
                 raise serializers.ValidationError(_("Circular dependency detected in parent hierarchy."))
            if parent:
                 visited.add(parent.id)
        return value

    # Removed validate method checking is_primary vs parent

# =============================================================================
# Account Serializers (Updated)
# =============================================================================

class AccountReadSerializer(serializers.ModelSerializer):
    """
    Serializer for *reading* Account data.
    Includes nested group, derived nature, and stored balance.
    """
    account_group = AccountGroupSummarySerializer(read_only=True)
    # Use the model fields directly, as they are now stored/managed
    account_nature = serializers.ChoiceField(choices=AccountNature.choices, read_only=True)
    current_balance = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    balance_last_updated = serializers.DateTimeField(read_only=True, allow_null=True)
    # Other fields for context
    account_type_display = serializers.CharField(source='get_account_type_display', read_only=True)
    currency_display = serializers.CharField(source='get_currency_display', read_only=True)
    control_account_party_type_display = serializers.CharField(source='get_control_account_party_type_display', read_only=True)

    class Meta:
        model = Account
        fields = [
            'id',
            'account_number',
            'account_name',
            'description',
            'account_group', # Nested summary
            'account_type', # Store the code
            'account_type_display', # Show the display name
            'account_nature', # Derived, read-only
            'currency', # Store the code
            'currency_display', # Show the display name
            'is_active',
            'allow_direct_posting',
            'is_control_account',
            'control_account_party_type', # Store the code
            'control_account_party_type_display', # Show the display name
            'current_balance', # Read stored balance
            'balance_last_updated', # Read timestamp
            'created_at',
            'updated_at',
        ]
        # Almost all fields are read-only in this specific serializer
        read_only_fields = fields


class AccountWriteSerializer(serializers.ModelSerializer):
    """
    Serializer for *creating/updating* Account data.
    Excludes system-managed fields like nature and balance.
    """
    account_group = serializers.PrimaryKeyRelatedField(
        queryset=AccountGroup.objects.all(),
        help_text=_("PK of the parent Account Group.")
    )
    # User provides the core classification and settings
    account_type = serializers.ChoiceField(choices=AccountType.choices)
    currency = serializers.ChoiceField(choices=CurrencyType.choices, default=CurrencyType.USD.name) # Provide a default
    control_account_party_type = serializers.ChoiceField(
        choices=PartyType.choices,
        allow_null=True, # Allow null if not a control account
        required=False # Not required if is_control_account is False
    )

    class Meta:
        model = Account
        fields = [
            'id', # Read-only, useful reference
            'account_number',
            'account_name',
            'description',
            'account_group', # Writable PK link
            'account_type', # User selects type
            'currency',
            'is_active',
            'allow_direct_posting',
            'is_control_account',
            'control_account_party_type',
        ]
        read_only_fields = ('id',)
        # Excluded: account_nature (derived), current_balance (calculated), balance_last_updated (calculated)

    def validate(self, data):
        """Object-level validation for account writes."""
        instance = getattr(self, 'instance', None)
        # Combine data for validation context if updating
        current_data = instance.__dict__ if instance else {}
        check_data = {**current_data, **data}

        is_control = check_data.get('is_control_account', False)
        party_type = check_data.get('control_account_party_type', None)

        # Use model's validation logic, but provide early feedback
        if is_control and not party_type:
            raise serializers.ValidationError({
                'control_account_party_type': _("This field is required when 'Is Control Account' is checked.")
            })
        if not is_control and party_type:
             raise serializers.ValidationError({
                 'control_account_party_type': _("Cannot set Party Type when 'Is Control Account' is unchecked.")
             })

        # Optional: Add validation if allow_direct_posting depends on group/parent settings

        return data # Return original payload data

    # Note: The actual setting of account_nature happens in Account.save()

# =============================================================================
# Ledger Serializers (Adding Here)
# =============================================================================

class AccountLedgerEntrySerializer(serializers.Serializer):
    """Serializer for a single line item in an Account Ledger report."""
    line_pk = serializers.IntegerField(read_only=True)
    date = serializers.DateField(read_only=True)
    voucher_pk = serializers.IntegerField(read_only=True)
    voucher_number = serializers.CharField(read_only=True)
    narration = serializers.CharField(read_only=True)
    reference = serializers.CharField(read_only=True, allow_blank=True)
    debit = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    credit = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    running_balance = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)

# Reusing AccountSummarySerializer defined earlier for the 'account' field

class AccountLedgerResponseSerializer(serializers.Serializer):
    """Serializer for the overall response of the Account Ledger endpoint."""
    account = AccountSummarySerializer(read_only=True)
    start_date = serializers.DateField(required=False, allow_null=True, read_only=True)
    end_date = serializers.DateField(required=False, allow_null=True, read_only=True)
    opening_balance = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    total_debit = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    total_credit = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    closing_balance = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    # 'entries' field is typically handled by pagination in the View,
    # but defining it here clarifies the intended structure if not paginating.
    # entries = AccountLedgerEntrySerializer(many=True, read_only=True)