from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from decimal import Decimal

# Project-specific imports
from crp_accounting.models import Party, Account
from crp_core.enums import PartyType
from .coa import AccountSummarySerializer


# --- Party Serializers ---

class PartyReadSerializer(serializers.ModelSerializer):
    """Serializer for reading Party data (GET requests)."""
    control_account = AccountSummarySerializer(read_only=True)
    balance = serializers.SerializerMethodField()
    credit_status = serializers.SerializerMethodField()
    party_type = serializers.CharField(source='get_party_type_display', read_only=True)

    class Meta:
        model = Party
        fields = [
            'id',
            'party_type',
            'name',
            'contact_email',
            'contact_phone',
            'address',
            'control_account',
            'credit_limit',
            'is_active',
            'balance',
            'credit_status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_balance(self, obj: Party) -> Decimal | None:
        try:
            return obj.calculate_outstanding_balance()
        except (ValueError, AttributeError):
            return None

    def get_credit_status(self, obj: Party) -> str:
        return obj.get_credit_status()


class PartyWriteSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Party data (POST/PUT/PATCH)."""
    control_account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.filter(is_active=True),
        allow_null=True,
        required=False,
        help_text=_("ID of the Control Account from the Chart of Accounts.")
    )
    party_type = serializers.ChoiceField(choices=PartyType.choices)

    class Meta:
        model = Party
        fields = [
            'id',
            'party_type',
            'name',
            'contact_email',
            'contact_phone',
            'address',
            'control_account',
            'credit_limit',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_credit_limit(self, value):
        if value < Decimal('0.00'):
            raise serializers.ValidationError(_("Credit limit cannot be negative."))
        return value

    def validate(self, data):
        instance = getattr(self, 'instance', None)

        party_type = data.get('party_type', getattr(instance, 'party_type', None))
        control_account = data.get('control_account', getattr(instance, 'control_account', None))
        is_active = data.get('is_active', getattr(instance, 'is_active', True))

        requires_control_account_types = [PartyType.CUSTOMER.value, PartyType.SUPPLIER.value]
        party_type_labels = {
            PartyType.CUSTOMER.value: "Customer",
            PartyType.SUPPLIER.value: "Supplier"
        }

        # Check for required control account if active and type needs it
        if is_active and party_type in requires_control_account_types and not control_account:
            raise serializers.ValidationError({
                'control_account': _("An active %(type)s must have a Control Account.") % {
                    'type': party_type_labels.get(party_type, party_type.capitalize())
                }
            })

        if control_account:
            if not control_account.is_control_account:
                raise serializers.ValidationError({
                    'control_account': _("The selected account is not designated as a control account.")
                })

            expected_party_type_for_account = getattr(control_account, 'control_account_party_type', None)

            if expected_party_type_for_account != party_type:
                raise serializers.ValidationError({
                    'control_account': _(
                        "The selected control account (%(account)s) is designated for '%(expected_type)s', not for '%(actual_type)s'."
                    ) % {
                        'account': control_account.account_name,
                        'expected_type': expected_party_type_for_account or 'N/A',
                        'actual_type': party_type
                    }
                })

        return data
