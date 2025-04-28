import logging
from collections import defaultdict
from decimal import Decimal
from datetime import date
from typing import List, Dict, Tuple, Optional, Any
from django.utils.translation import gettext_lazy as _
from django.db import models
from django.db.models import Sum, Q, Value, F
from django.db.models.functions import Coalesce
from django.core.exceptions import ObjectDoesNotExist

# --- Model Imports ---
from ..models.coa import Account, AccountGroup, PLSection
from ..models.journal import VoucherLine, TransactionStatus, DrCrType
from crp_core.enums import AccountNature, AccountType

logger = logging.getLogger(__name__)

# --- Constants ---
ZERO_DECIMAL = Decimal('0.00')

# =============================================================================
# Trial Balance Service Function (FINAL - V3.3 Logic)
# =============================================================================

def generate_trial_balance_structured(as_of_date: date) -> Dict[str, Any]:
    """
    Generates a structured Trial Balance as of a specific date, using bulk aggregation
    for performance and including account group hierarchy. Handles ungrouped accounts.
    Always includes all active accounts, regardless of zero balance.

    Args:
        as_of_date: The date for which the Trial Balance is generated (inclusive).

    Returns:
        A dictionary containing:
        - 'as_of_date': The date provided.
        - 'hierarchy': List[dict] - Hierarchical structure of groups and accounts.
                       Format: {'id', 'name', 'type'('group'|'account'), 'level',
                                'debit', 'credit', 'children': []}
        - 'flat_entries': List[dict] - Flat list of *all* active accounts.
                          {'account_pk', 'account_number', 'account_name', 'debit', 'credit'}
        - 'total_debit': Decimal - Sum of all debit balances from initial aggregation.
        - 'total_credit': Decimal - Sum of all credit balances from initial aggregation.
        - 'is_balanced': bool - True if total_debit == total_credit.
    """
    logger.info(f"Generating Structured Trial Balance as of {as_of_date}...")

    # --- 1. Bulk Calculate Balances for All Active Accounts ---
    # Filter relevant posted lines
    posted_lines = VoucherLine.objects.filter(
        voucher__status=TransactionStatus.POSTED,
        voucher__date__lte=as_of_date,
        account__is_active=True # Important: Base calculation on active accounts
    )

    # Aggregate Debits and Credits per account
    account_balances_data = posted_lines.values(
        'account' # Group by account PK
    ).annotate(
        account_pk=F('account__id'),
        account_number=F('account__account_number'),
        account_name=F('account__account_name'),
        account_nature=F('account__account_nature'),
        account_group_pk=F('account__account_group_id'),
        total_debit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.DEBIT.name)),
            ZERO_DECIMAL, output_field=models.DecimalField()
        ),
        total_credit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.CREDIT.name)),
            ZERO_DECIMAL, output_field=models.DecimalField()
        )
    ).values(
        'account_pk', 'account_number', 'account_name', 'account_nature',
        'account_group_pk', 'total_debit', 'total_credit'
    )

    # --- Process aggregated results into a lookup dictionary ---
    # Structure: { account_pk: {'number': ..., 'name': ..., 'debit': ..., 'credit': ..., 'group_pk': ...} }
    # This dictionary will hold the calculated debit/credit balance for each account with activity.
    account_balances: Dict[int, Dict[str, Any]] = {}
    flat_entries_list: List[Dict[str, Any]] = []
    grand_total_debit = ZERO_DECIMAL # Totals from accounts with activity
    grand_total_credit = ZERO_DECIMAL

    for item in account_balances_data:
        pk = item['account_pk']
        nature = item['account_nature']
        debit_total = item['total_debit']
        credit_total = item['total_credit']

        # Calculate final balance based on nature
        if nature == AccountNature.DEBIT.name:
            balance = debit_total - credit_total
        elif nature == AccountNature.CREDIT.name:
            balance = credit_total - debit_total
        else:
            logger.warning(f"Account PK {pk} has invalid nature '{nature}'. Assigning zero balance.")
            balance = ZERO_DECIMAL

        # Determine final Debit/Credit column values based on balance sign and nature
        debit_amount = ZERO_DECIMAL
        credit_amount = ZERO_DECIMAL
        if balance > ZERO_DECIMAL:
            if nature == AccountNature.DEBIT.name: debit_amount = balance
            else: credit_amount = balance
        elif balance < ZERO_DECIMAL:
            if nature == AccountNature.DEBIT.name: credit_amount = -balance
            else: debit_amount = -balance

        # Store processed balance info
        account_balances[pk] = {
            'account_number': item['account_number'],
            'account_name': item['account_name'],
            'debit': debit_amount,
            'credit': credit_amount,
            'group_pk': item['account_group_pk'],
        }
        # Add to flat list (initially only includes accounts with activity)
        flat_entry = {
             'account_pk': pk,
             'account_number': item['account_number'],
             'account_name': item['account_name'],
             'debit': debit_amount,
             'credit': credit_amount,
        }
        flat_entries_list.append(flat_entry)

        grand_total_debit += debit_amount
        grand_total_credit += credit_amount

    # --- 2. Ensure ALL Active Accounts are included (even with Zero Balance) ---
    # Fetch all active accounts (needed for hierarchy and zero-balance inclusion)
    all_active_accounts_qs = Account.objects.filter(is_active=True).select_related('account_group').order_by('account_number')
    accounts_with_balances_pks = set(account_balances.keys())

    for acc in all_active_accounts_qs:
        if acc.pk not in accounts_with_balances_pks:
            # This active account had no posted transactions, add with zero balance
            pk = acc.pk
            zero_entry_data = {
                'account_number': acc.account_number,
                'account_name': acc.account_name,
                'debit': ZERO_DECIMAL,
                'credit': ZERO_DECIMAL,
                'group_pk': acc.account_group_id,
            }
            # Add to main balances dict for hierarchy builder
            account_balances[pk] = zero_entry_data
            # Also add to the flat list
            flat_entry = {
                 'account_pk': pk,
                 'account_number': acc.account_number,
                 'account_name': acc.account_name,
                 'debit': ZERO_DECIMAL,
                 'credit': ZERO_DECIMAL,
            }
            flat_entries_list.append(flat_entry)
            # Grand totals remain unchanged as balance is zero

    # Sort the complete flat list
    flat_entries_list.sort(key=lambda x: x['account_number'])

    # --- 3. Build Hierarchy ---
    # Fetch all groups once
    groups = AccountGroup.objects.all().order_by('name')
    group_dict = {group.pk: group for group in groups}

    hierarchy, _, _ = _build_group_hierarchy_recursive_v3_4(  # <<< CHANGE HERE
        parent_id=None,
        all_groups=group_dict,
        account_balances=account_balances,
        level=0  # Start top level items at level 0
    )

    # --- 4. Final Balance Check (using totals derived from active accounts) ---
    is_balanced = grand_total_debit == grand_total_credit
    if not is_balanced:
        logger.error(f"STRUCTURED TRIAL BALANCE OUT OF BALANCE! Date: {as_of_date}, Aggregated Debits: {grand_total_debit}, Credits: {grand_total_credit}")

    logger.info(f"Structured Trial Balance generated. Date: {as_of_date}, Debits: {grand_total_debit}, Credits: {grand_total_credit}, Balanced: {is_balanced}")

    # --- 5. Return Results ---
    # The service always returns the full data including zero-balance accounts.
    # Filtering happens in the View if requested.
    return {
        'as_of_date': as_of_date,
        'hierarchy': hierarchy,
        'flat_entries': flat_entries_list,
        'total_debit': grand_total_debit, # Report totals derived from aggregation
        'total_credit': grand_total_credit,
        'is_balanced': is_balanced,
    }

# =============================================================================
# Hierarchy Helper Function (Recursive - FINAL - V3.3 Logic)
# =============================================================================

def _build_group_hierarchy_recursive_v3_4( # Renamed for clarity
    parent_id: Optional[int],
    all_groups: Dict[int, AccountGroup],
    account_balances: Dict[int, Dict], # Contains ALL active accounts now
    level: int # Represents the level of the nodes being generated in this call
) -> Tuple[List[Dict], Decimal, Decimal]: # Return hierarchy, total_debit, total_credit
    """
    Recursive helper V3.4: Builds hierarchy and calculates/returns correct subtotals.
    Assigns level based on the current processing depth. Both child groups and
    direct accounts under a parent are assigned the same level in the returned list.
    """
    current_level_nodes: List[Dict] = []
    current_level_total_debit = ZERO_DECIMAL
    current_level_total_credit = ZERO_DECIMAL

    # --- 1. Process Child Groups Recursively ---
    # Find groups whose parent is the current parent_id
    child_groups = [group for pk, group in all_groups.items() if group.parent_group_id == parent_id]

    for group in sorted(child_groups, key=lambda g: g.name):
        # Recursive call gets children nodes (at level + 1) and their totals
        child_hierarchy_nodes, child_total_debit, child_total_credit = _build_group_hierarchy_recursive_v3_4(
            parent_id=group.pk,
            all_groups=all_groups,
            account_balances=account_balances,
            level=level + 1 # Children of this group start at the next level
        )

        # Create node for this group at the CURRENT level
        group_node: Dict[str, Any] = {
            'id': group.pk,
            'name': group.name,
            'type': 'group',
            'level': level, # This group node is at the current level
            'debit': child_total_debit, # Use totals returned from children
            'credit': child_total_credit,
            'children': child_hierarchy_nodes # Assign children structure
        }

        # Include the group node if it has children OR its own calculated totals are non-zero
        if group_node['children'] or group_node['debit'] != ZERO_DECIMAL or group_node['credit'] != ZERO_DECIMAL:
            current_level_nodes.append(group_node)
            # Accumulate this child group's totals into the totals for the *current* level being processed
            current_level_total_debit += child_total_debit
            current_level_total_credit += child_total_credit

    # --- 2. Process Accounts Directly Under This Parent Group ---
    direct_accounts_nodes = []
    for acc_pk, acc_data in account_balances.items():
        # Check if the account's group matches the current parent_id
        if acc_data['group_pk'] == parent_id:
            # Create the node for this account also at the CURRENT level
            account_node = {
                'id': acc_pk,
                'name': f"{acc_data['account_number']} - {acc_data['account_name']}",
                'type': 'account',
                'level': level, # <<< Accounts are peers to sibling groups at this level
                'debit': acc_data['debit'],
                'credit': acc_data['credit'],
                'children': []
            }
            direct_accounts_nodes.append(account_node)
            # Accumulate this account's totals into the totals for the *current* level
            current_level_total_debit += acc_data['debit']
            current_level_total_credit += acc_data['credit']

    # Sort direct accounts by number
    direct_accounts_nodes.sort(key=lambda item: account_balances[item['id']]['account_number'])

    # Combine groups and direct accounts for this level
    # Add sorted groups first, then sorted accounts
    current_level_nodes.extend(direct_accounts_nodes)

    # --- 3. Return the built nodes for this level and its calculated totals ---
    return current_level_nodes, current_level_total_debit, current_level_total_credit
# =============================================================================
# Profit and Loss Service Function (Refactored for Structure)
# =============================================================================

def generate_profit_loss_structured(start_date: date, end_date: date) -> Dict[str, Any]:
    """
    Generates a structured Profit and Loss (Income Statement) for a period,
    including Gross Profit calculation based on PLSection classification.

    Args:
        start_date: The start date of the reporting period (inclusive).
        end_date: The end date of the reporting period (inclusive).

    Returns:
        A dictionary containing:
        - 'start_date': The report start date.
        - 'end_date': The report end date.
        - 'report_structure': List[dict] - Structured P&L sections.
            Node Format: {'section_key': PLSection value (e.g., 'REVENUE'),
                          'title': Display title (e.g., 'Revenue'),
                          'is_subtotal': bool, # True for Gross Profit, Net Income etc.
                          'total': Decimal, # Total amount for the section/subtotal
                          'nodes': List[dict] # Hierarchical breakdown within the section
                         }
            Node breakdown format (within 'nodes'):
                         {'id', 'name', 'type'('group'|'account'), 'level',
                          'amount', # Net movement for account/group within section
                          'children': []}
        - Other summary fields (optional, as structure contains totals):
          'total_revenue', 'total_cogs', 'gross_profit', 'total_opex',
          'total_other_income', 'total_other_expense', 'total_tax', 'net_income'
    """
    logger.info(f"Generating Structured P&L statement for period: {start_date} to {end_date}")

    # --- 1. Aggregate Net Movement for ALL P&L Account Types ---
    # Filter relevant posted lines within the date range and for P&L account types
    relevant_lines = VoucherLine.objects.filter(
        voucher__status=TransactionStatus.POSTED,
        voucher__date__gte=start_date,
        voucher__date__lte=end_date,
        account__is_active=True,
        account__account_type__in=[ # Include all types that appear on P&L
            AccountType.INCOME.value,
            AccountType.EXPENSE.value,
            AccountType.COST_OF_GOODS_SOLD.value
        ]
    )

    # Group by account and calculate total debits/credits, include pl_section
    account_movements_data = relevant_lines.values(
        'account' # Group by account PK
    ).annotate(
        account_pk=F('account__id'),
        account_number=F('account__account_number'),
        account_name=F('account__account_name'),
        account_type=F('account__account_type'),
        account_group_pk=F('account__account_group_id'),
        pl_section=F('account__pl_section'), # <<< Fetch the P&L section
        period_debit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.DEBIT.value)), # Use .value
            ZERO_DECIMAL, output_field=models.DecimalField()
        ),
        period_credit=Coalesce(
            Sum('amount', filter=Q(dr_cr=DrCrType.CREDIT.value)), # Use .value
            ZERO_DECIMAL, output_field=models.DecimalField()
        )
    ).values(
        'account_pk', 'account_number', 'account_name', 'account_type',
        'account_group_pk', 'pl_section', # <<< Include pl_section in final values
        'period_debit', 'period_credit'
    )

    # --- 2. Process Results & Calculate Section Totals ---
    # Store details per account for hierarchy building
    account_details_by_pk: Dict[int, Dict[str, Any]] = {}
    # Store totals per P&L section
    section_totals: Dict[str, Decimal] = defaultdict(Decimal) # Keyed by PLSection value

    for item in account_movements_data:
        pk = item['account_pk']
        acc_type = item['account_type']
        pl_section_value = item['pl_section']
        debit_total = item['period_debit']
        credit_total = item['period_credit']
        net_movement = ZERO_DECIMAL

        # Calculate net movement contribution (positive = increase P&L / favorable)
        # Income/Other Income: Credit balance increases P&L (Cr - Dr)
        # COGS/Expense/Other Expense/Tax: Debit balance decreases P&L (Dr - Cr is the cost)
        # We store the 'natural' movement magnitude here, sign handled by section logic later
        if acc_type == AccountType.INCOME.value:
            net_movement = credit_total - debit_total
        elif acc_type in [AccountType.EXPENSE.value, AccountType.COST_OF_GOODS_SOLD.value]:
            net_movement = debit_total - credit_total # Store cost/expense as positive value
        else:
            logger.warning(f"Unexpected account type '{acc_type}' found for Account PK {pk} in P&L calculation.")
            continue

        # Store details only if there's movement
        if net_movement != ZERO_DECIMAL:
            account_details_by_pk[pk] = {
                'account_number': item['account_number'],
                'account_name': item['account_name'],
                'amount': net_movement, # Store net change magnitude
                'group_pk': item['account_group_pk'],
                'pl_section': pl_section_value,
                'account_type': acc_type
            }
            # Accumulate totals for the specific P&L section
            if pl_section_value and pl_section_value != PLSection.NONE.value:
                section_totals[pl_section_value] += net_movement

    # --- Optional: Include Zero-Activity P&L Accounts (If Required) ---
    # Add logic here similar to Trial Balance step 1d/1e if needed,
    # fetching active P&L accounts not in account_details_by_pk and adding
    # them with amount=0 to account_details_by_pk.

    # --- 3. Calculate P&L Subtotals ---
    total_revenue = section_totals.get(PLSection.REVENUE.value, ZERO_DECIMAL)
    total_cogs = section_totals.get(PLSection.COGS.value, ZERO_DECIMAL)
    total_opex = section_totals.get(PLSection.OPERATING_EXPENSE.value, ZERO_DECIMAL)
    total_other_income = section_totals.get(PLSection.OTHER_INCOME.value, ZERO_DECIMAL)
    total_other_expense = section_totals.get(PLSection.OTHER_EXPENSE.value, ZERO_DECIMAL)
    total_tax = section_totals.get(PLSection.TAX_EXPENSE.value, ZERO_DECIMAL)

    gross_profit = total_revenue - total_cogs
    # Operating Profit (Optional, depends on standard)
    operating_profit = gross_profit - total_opex
    # Profit Before Tax (Using Operating Profit as base)
    profit_before_tax = operating_profit + total_other_income - total_other_expense
    net_income = profit_before_tax - total_tax

    # --- 4. Build Structured Report Output ---
    report_structure: List[Dict[str, Any]] = []

    # Fetch relevant groups (needed for hierarchy within sections)
    relevant_group_pks = set(d['group_pk'] for d in account_details_by_pk.values() if d['group_pk'])
    # Find all ancestor groups efficiently (similar to Trial Balance logic)
    parent_pks = set()
    temp_pks = relevant_group_pks.copy()
    while temp_pks:
        parents = AccountGroup.objects.filter(pk__in=temp_pks).values_list('parent_group_id', flat=True)
        new_parents = set(p for p in parents if p is not None)
        discovered_ancestors = new_parents - relevant_group_pks
        if not discovered_ancestors: break # No new ancestors found
        parent_pks.update(discovered_ancestors)
        temp_pks = discovered_ancestors
    relevant_group_pks.update(parent_pks)

    if relevant_group_pks:
         groups = AccountGroup.objects.filter(pk__in=relevant_group_pks).order_by('name')
         group_dict = {group.pk: group for group in groups}
    else:
         group_dict = {}


    # -- Define Section Order and Titles --
    # Order matters for presentation
    pnl_section_order = [
        (PLSection.REVENUE, _("Revenue")),
        (PLSection.COGS, _("Cost of Goods Sold")),
        # Gross Profit is inserted manually
        (PLSection.OPERATING_EXPENSE, _("Operating Expenses")),
        # Operating Profit inserted manually (optional)
        (PLSection.OTHER_INCOME, _("Other Income")),
        (PLSection.OTHER_EXPENSE, _("Other Expenses")),
        # Profit Before Tax inserted manually
        (PLSection.TAX_EXPENSE, _("Tax Expense")),
        # Net Income inserted manually
    ]

    # -- Helper function to build hierarchy within a section --
    def build_section_nodes(section_key: str) -> Tuple[List[Dict], Decimal]:
        """Builds hierarchy for accounts belonging ONLY to the given PL Section."""
        section_account_details = {
            pk: data for pk, data in account_details_by_pk.items()
            if data['pl_section'] == section_key
        }
        if not section_account_details:
            return [], ZERO_DECIMAL

        # Use a helper similar to Trial Balance, passing ONLY relevant accounts
        section_hierarchy, section_total = _build_pnl_item_hierarchy_recursive(
            parent_id=None,
            all_groups=group_dict,
            account_items=section_account_details, # Pass section-specific items
            level=0
        )
        # Verify calculated total matches pre-calculated section total
        precalculated_total = section_totals.get(section_key, ZERO_DECIMAL)
        if section_total != precalculated_total:
            logger.warning(f"P&L hierarchy subtotal mismatch for section {section_key}. "
                           f"Hierarchy: {section_total}, Aggregated: {precalculated_total}. Using aggregated.")
        return section_hierarchy, precalculated_total # Return pre-calculated total for consistency


    # -- Assemble the structure section by section --
    for section_enum, title in pnl_section_order:
        section_key = section_enum.value
        nodes, total = build_section_nodes(section_key)

        # Add section if it has nodes or a non-zero total
        if nodes or total != ZERO_DECIMAL:
            report_structure.append({
                'section_key': section_key,
                'title': str(title), # Ensure title is string
                'is_subtotal': False,
                'total': total,
                'nodes': nodes
            })

        # --- Insert Subtotals Manually After Relevant Sections ---
        if section_enum == PLSection.COGS:
            report_structure.append({
                'section_key': 'GROSS_PROFIT',
                'title': str(_("Gross Profit")),
                'is_subtotal': True,
                'total': gross_profit,
                'nodes': []
            })
        elif section_enum == PLSection.OPERATING_EXPENSE:
             # Optional: Add Operating Profit
             # report_structure.append({
             #     'section_key': 'OPERATING_PROFIT',
             #     'title': str(_("Operating Profit")),
             #     'is_subtotal': True,
             #     'total': operating_profit,
             #     'nodes': []
             # })
             pass # Skipping Operating Profit for now
        elif section_enum == PLSection.OTHER_EXPENSE:
             report_structure.append({
                 'section_key': 'PROFIT_BEFORE_TAX',
                 'title': str(_("Profit Before Tax")),
                 'is_subtotal': True,
                 'total': profit_before_tax,
                 'nodes': []
             })

    # --- Add Final Net Income Subtotal ---
    report_structure.append({
        'section_key': 'NET_INCOME',
        'title': str(_("Net Income / (Loss)")),
        'is_subtotal': True,
        'total': net_income,
        'nodes': []
    })


    logger.info(f"Structured P&L generated. Period: {start_date}-{end_date}, Net Income: {net_income}")

    # Return comprehensive dictionary
    return {
        'start_date': start_date,
        'end_date': end_date,
        'report_structure': report_structure,
        # Include summary totals for convenience/verification
        'total_revenue': total_revenue,
        'total_cogs': total_cogs,
        'gross_profit': gross_profit,
        'total_opex': total_opex,
        'total_other_income': total_other_income,
        'total_other_expense': total_other_expense,
        'operating_profit': operating_profit, # Include if calculated
        'profit_before_tax': profit_before_tax,
        'total_tax': total_tax,
        'net_income': net_income,
    }


# =============================================================================
# P&L Hierarchy Helper Function (Similar to Trial Balance)
# =============================================================================

def _build_pnl_item_hierarchy_recursive(
    parent_id: Optional[int],
    all_groups: Dict[int, AccountGroup],
    account_items: Dict[int, Dict], # Dict of account PK -> {amount, group_pk, etc.} for THIS SECTION ONLY
    level: int
) -> Tuple[List[Dict], Decimal]: # Return hierarchy nodes and total amount for this branch
    """
    Recursive helper for P&L sections: Builds hierarchy for a SUBSET of accounts
    (belonging to one PL section) and calculates group subtotals based on net movement.
    """
    current_level_nodes: List[Dict] = []
    current_branch_total_amount = ZERO_DECIMAL

    # --- 1. Process Child Groups Recursively ---
    child_groups = [group for pk, group in all_groups.items() if group.parent_group_id == parent_id]

    for group in sorted(child_groups, key=lambda g: g.name):
        # Recursive call gets children nodes and their totals *within this section*
        child_hierarchy_nodes, child_total_amount = _build_pnl_item_hierarchy_recursive(
            parent_id=group.pk,
            all_groups=all_groups,
            account_items=account_items, # Pass the same subset of accounts down
            level=level + 1
        )

        # Create node for this group if it contributed to this section's total
        if child_total_amount != ZERO_DECIMAL or any(acc['group_pk'] == group.pk for acc in account_items.values()):
             group_node: Dict[str, Any] = {
                 'id': group.pk,
                 'name': group.name,
                 'type': 'group',
                 'level': level,
                 'amount': child_total_amount,
                 'children': child_hierarchy_nodes
             }
             current_level_nodes.append(group_node)
             current_branch_total_amount += child_total_amount

    # --- 2. Process Accounts Directly Under This Parent Group (within this section) ---
    direct_accounts_nodes = []
    # Sort accounts by number before processing
    relevant_account_pks = sorted(
        [pk for pk, data in account_items.items() if data['group_pk'] == parent_id],
        key=lambda pk: account_items[pk]['account_number'] # Use number from the dict
    )

    for acc_pk in relevant_account_pks:
        acc_data = account_items[acc_pk]
        account_node = {
            'id': acc_pk,
            'name': f"{acc_data['account_number']} - {acc_data['account_name']}",
            'type': 'account',
            'level': level, # Accounts are peers to sibling groups at this level
            'amount': acc_data['amount'],
            'children': []
        }
        direct_accounts_nodes.append(account_node)
        current_branch_total_amount += acc_data['amount']

    # Combine groups and accounts for this level, groups first
    current_level_nodes.extend(direct_accounts_nodes)

    return current_level_nodes, current_branch_total_amount