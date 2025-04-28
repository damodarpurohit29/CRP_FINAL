from rest_framework import serializers
from django.utils import timezone
from crp_accounting.models.period import FiscalYear, AccountingPeriod


class FiscalYearSerializer(serializers.ModelSerializer):
    """
    Serializer for the FiscalYear model.
    Enforces logical constraints and prevents modifications on closed years.
    """
    class Meta:
        model = FiscalYear
        fields = [
            'id', 'name', 'start_date', 'end_date',
            'status', 'is_active', 'closed_by', 'closed_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['status', 'is_active', 'closed_by', 'closed_at', 'created_at', 'updated_at']

    def validate(self, data):
        """
        Ensure end date is after start date.
        """
        start_date = data.get('start_date', self.instance.start_date if self.instance else None)
        end_date = data.get('end_date', self.instance.end_date if self.instance else None)

        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError("End date must be after start date.")
        return data

    def update(self, instance, validated_data):
        """
        Prevent updates if the fiscal year is closed.
        """
        if instance.status == 'Closed':
            raise serializers.ValidationError("Cannot update a closed fiscal year.")
        return super().update(instance, validated_data)


class AccountingPeriodSerializer(serializers.ModelSerializer):
    """
    Serializer for the AccountingPeriod model.
    Includes validation for date range and lock status.
    """
    class Meta:
        model = AccountingPeriod
        fields = [
            'id', 'fiscal_year',
            'start_date', 'end_date', 'locked',

        ]
        read_only_fields = ['locked']

    def validate(self, data):
        """
        Validate date consistency and period within fiscal year.
        """
        start_date = data.get('start_date', self.instance.start_date if self.instance else None)
        end_date = data.get('end_date', self.instance.end_date if self.instance else None)
        fiscal_year = data.get('fiscal_year', self.instance.fiscal_year if self.instance else None)

        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError("End date must be after start date.")

        if fiscal_year:
            if start_date < fiscal_year.start_date or end_date > fiscal_year.end_date:
                raise serializers.ValidationError("Accounting period must be within the fiscal year range.")

        return data

    def update(self, instance, validated_data):
        """
        Prevent updates if the period is locked.
        """
        if instance.is_locked:
            raise serializers.ValidationError("Cannot modify a locked accounting period.")
        return super().update(instance, validated_data)
