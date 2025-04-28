# crp_accounting/serializers/profit_loss.py

import logging
from rest_framework import serializers
from decimal import Decimal

logger = logging.getLogger(__name__)

# =============================================================================
# P&L Hierarchy Node Serializer (CORRECTED)
# =============================================================================

class ProfitLossHierarchyNodeSerializer(serializers.Serializer):
    """
    Serializes a node (Account Group or Account) within the hierarchical breakdown
    of a specific Profit & Loss statement section (e.g., within Revenue or OpEx).
    """
    id = serializers.IntegerField(
        read_only=True,
        help_text="Primary key of the Account or Account Group."
    )
    name = serializers.CharField(
        read_only=True,
        help_text="Name of the Account Group or formatted 'AccountNumber - AccountName'."
    )
    type = serializers.ChoiceField(
        choices=['group', 'account'],
        read_only=True,
        help_text="Indicates if the node is a group or an account."
    )
    level = serializers.IntegerField(
        read_only=True,
        help_text="Hierarchy level (depth) for display indentation."
    )
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2,
        read_only=True,
        help_text="Net movement amount for this account or subtotal for this group within the section for the period."
    )
    # --- CORRECTED: Remove incorrect 'child' argument ---
    children = serializers.ListField(
        # No 'child' argument needed here, get_fields handles it.
        read_only=True,
        help_text="List of child nodes belonging to this group node within the section."
    )
    # --- END CORRECTION ---

    def get_fields(self):
        """Dynamically defines the 'children' field for recursion."""
        fields = super().get_fields()
        # This line correctly sets the child serializer for the 'children' field.
        fields['children'] = ProfitLossHierarchyNodeSerializer(many=True, read_only=True)
        return fields

    class Meta:
        ref_name = "ProfitLossHierarchyNode"


# =============================================================================
# P&L Section Item Serializer (No change needed)
# =============================================================================
class ProfitLossSectionItemSerializer(serializers.Serializer):
    # ... (this serializer remains the same) ...
    section_key = serializers.CharField(read_only=True, help_text="...")
    title = serializers.CharField(read_only=True, help_text="...")
    is_subtotal = serializers.BooleanField(read_only=True, help_text="...")
    total = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True, help_text="...")
    nodes = ProfitLossHierarchyNodeSerializer(many=True, read_only=True, help_text="...")

    class Meta:
        ref_name = "ProfitLossSectionItem"


# =============================================================================
# P&L Structured Response Serializer (Top Level)
# =============================================================================
class ProfitLossStructuredResponseSerializer(serializers.Serializer):
    # ... (this serializer remains the same) ...
    start_date = serializers.DateField(read_only=True, help_text="...")
    end_date = serializers.DateField(read_only=True, help_text="...")
    report_structure = ProfitLossSectionItemSerializer(many=True, read_only=True, help_text="...")
    total_revenue = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True, help_text="...")
    # ... other summary fields ...
    net_income = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True, help_text="...")

    class Meta:
        ref_name = "ProfitLossStructuredResponse"