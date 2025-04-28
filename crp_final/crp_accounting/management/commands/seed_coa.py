# crp_accounting/management/commands/seed_coa.py
import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError, models
from django.core.exceptions import ValidationError

# --- Project-Specific Imports ---
try:
    from crp_accounting.models.coa import AccountGroup, Account, PLSection # <<< ADDED PLSection Import
    from crp_core.constants import ACCOUNT_NATURE, ACCOUNT_ROLE_GROUPS
    from crp_core.enums import AccountType, AccountNature, PartyType, CurrencyType
except ImportError as e:
    raise CommandError(f"Could not import necessary modules. Check paths and dependencies: {e}")
except AttributeError as e:
     raise CommandError(f"AttributeError during import/setup. Have you defined all required Enums? Error: {e}")


logger = logging.getLogger(__name__)

# --- Helper Function ---
def get_primary_group_name(constant_key):
    """Extracts the primary concept (Assets, Liabilities, etc.) from the constant key."""
    return constant_key.split(' - ')[0].split(' (')[0].split(' / ')[0].strip()

# --- Derived Mappings ---

# Map primary group concepts to AccountType enum members
GROUP_CONCEPT_TO_ACCOUNT_TYPE = {
    'Assets': AccountType.ASSET,
    'Liabilities': AccountType.LIABILITY,
    'Equity': AccountType.EQUITY,
    'Income': AccountType.INCOME,
    'Cost of Goods Sold': AccountType.COST_OF_GOODS_SOLD,
    'Expenses': AccountType.EXPENSE,
    'Non-Operational / Adjustments': AccountType.EXPENSE, # Map this explicitly if needed
    'Taxation': AccountType.LIABILITY, # Assuming Taxation group holds liability accounts like "VAT Payable"
    'Receivables': AccountType.ASSET,
    'Payables': AccountType.LIABILITY,
}

# --- *** NEW MAPPING: Account Type to Default P&L Section *** ---
# This provides a baseline. Specific accounts can override this if needed.
# Using Enum Members for keys and PLSection Enum Members for values
ACCOUNT_TYPE_TO_DEFAULT_PL_SECTION = {
    AccountType.ASSET: PLSection.NONE,
    AccountType.LIABILITY: PLSection.NONE,
    AccountType.EQUITY: PLSection.NONE,
    AccountType.INCOME: PLSection.REVENUE, # Default income type to REVENUE section
    AccountType.COST_OF_GOODS_SOLD: PLSection.COGS, # Map COGS type to COGS section
    AccountType.EXPENSE: PLSection.OPERATING_EXPENSE, # Default expense type to OPERATING section
}
# --- *** END NEW MAPPING *** ---

# --- Revised Nature Mapping Derivation ---
ACCOUNT_TYPE_TO_NATURE_NAME = {
    AccountType.ASSET: AccountNature.DEBIT.name,
    AccountType.EXPENSE: AccountNature.DEBIT.name,
    AccountType.COST_OF_GOODS_SOLD: AccountNature.DEBIT.name,
    AccountType.LIABILITY: AccountNature.CREDIT.name,
    AccountType.EQUITY: AccountNature.CREDIT.name,
    AccountType.INCOME: AccountNature.CREDIT.name,
}

# Optional Consistency Check (no change needed here)
# ... (rest of consistency check logic) ...

# --- CONTROL_ACCOUNTS_MAP ---
CONTROL_ACCOUNTS_MAP = {
    '1030_accounts_receivable': PartyType.CUSTOMER.name,
    '2000_accounts_payable': PartyType.SUPPLIER.name,
    '9000_due_from_customers': PartyType.CUSTOMER.name,
    '9100_due_to_suppliers': PartyType.SUPPLIER.name,
}


class Command(BaseCommand):
    """
    Seeds/Updates the Chart of Accounts structure including P&L sections.
    Idempotent using update_or_create.
    """
    help = 'Seeds or updates the database with the default Chart of Accounts structure and P&L sections.'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('--- Starting Chart of Accounts Seeding/Update ---'))
        created_groups_count, updated_groups_count, skipped_groups_count = 0, 0, 0
        created_accounts_count, updated_accounts_count, skipped_accounts_count = 0, 0, 0
        group_objects_map = {}

        # --- Group Creation Loop ---
        for group_name_from_constant, roles in ACCOUNT_ROLE_GROUPS.items():
            self.stdout.write(f"\nProcessing Constant Group: '{group_name_from_constant}'")

            primary_name = get_primary_group_name(group_name_from_constant)
            parent_group_obj = group_objects_map.get(primary_name)

            # Create/Get Primary Group
            if not parent_group_obj:
                group_defaults = {'parent_group': None}
                try:
                    parent_group_obj, created = AccountGroup.objects.update_or_create(
                        name=primary_name, defaults=group_defaults
                    )
                    group_objects_map[primary_name] = parent_group_obj
                    if created: created_groups_count += 1; self.stdout.write(f"  [GROUP] Created primary: '{primary_name}'")
                    else: skipped_groups_count += 1 # Assuming skipped means found/no update needed
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"  [ERROR] Failed create/update primary group '{primary_name}': {e}. Skipping."))
                    continue

            # Create/Update Specific Group
            sub_group_defaults = {'parent_group': parent_group_obj}
            try:
                sub_group_obj, created = AccountGroup.objects.update_or_create(
                    name=group_name_from_constant, defaults=sub_group_defaults
                )
                group_objects_map[group_name_from_constant] = sub_group_obj
                if created: created_groups_count += 1; self.stdout.write(f"  [GROUP] Created group: '{group_name_from_constant}' (Parent: {primary_name})")
                else: skipped_groups_count += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  [ERROR] Failed create/update group '{group_name_from_constant}': {e}. Skipping its accounts."))
                continue

            # Determine Account Type for accounts in this group
            account_type_member = GROUP_CONCEPT_TO_ACCOUNT_TYPE.get(primary_name)
            if not account_type_member:
                 self.stderr.write(self.style.WARNING(f"  [WARN] Undetermined AccountType for primary concept '{primary_name}'. Skipping accounts in '{group_name_from_constant}'."))
                 continue

            # --- *** Determine Default PL Section for this group's accounts *** ---
            default_pl_section_member = ACCOUNT_TYPE_TO_DEFAULT_PL_SECTION.get(account_type_member, PLSection.NONE)
            # --- *** END Determine PL Section *** ---

            # --- Account Creation/Update Loop ---
            for account_code, account_name in roles:
                account_code_clean = account_code.strip()
                account_name_clean = account_name.strip()

                is_control = account_code_clean in CONTROL_ACCOUNTS_MAP
                control_party_type_name = CONTROL_ACCOUNTS_MAP.get(account_code_clean)

                # --- *** Start with the default P&L section based on type *** ---
                final_pl_section_member = default_pl_section_member
                # --- *** END Default *** ---

                # --- *** Add Overrides for Specific Accounts if needed *** ---
                # Example: Classify specific expense accounts
                if account_type_member == AccountType.EXPENSE:
                    if 'tax' in account_name_clean.lower():
                        final_pl_section_member = PLSection.TAX_EXPENSE
                    elif 'interest expense' in account_name_clean.lower(): # Be specific
                        final_pl_section_member = PLSection.OTHER_EXPENSE
                    # Add more specific overrides based on name/code if the default isn't right
                    # elif account_code_clean == '6XXX': final_pl_section_member = PLSection.OTHER_EXPENSE
                elif account_type_member == AccountType.INCOME:
                    if 'interest income' in account_name_clean.lower(): # Be specific
                        final_pl_section_member = PLSection.OTHER_INCOME
                    # Add more specific overrides if needed
                # --- *** END Overrides *** ---

                account_defaults = {
                    'account_name': account_name_clean,
                    'account_group': sub_group_obj,
                    'account_type': account_type_member.value, # Use .value for db choice
                    'currency': CurrencyType.INR.value, # Use .value for db choice
                    'pl_section': final_pl_section_member.value, # <<< ADDED: Use .value for db choice
                    'description': '',
                    'allow_direct_posting': True,
                    'is_active': True,
                    'is_control_account': is_control,
                    'control_account_party_type': control_party_type_name,
                }

                try:
                    account_obj, created = Account.objects.update_or_create(
                        account_number=account_code_clean,
                        defaults=account_defaults
                    )
                    if created:
                        created_accounts_count += 1
                    else:
                        # Only count as updated if something *actually* changed.
                        # update_or_create doesn't easily tell us this.
                        # If you want accurate update counts, you'd need to fetch first, compare, then save.
                        # For simplicity, we'll assume non-created means potentially updated or skipped.
                        updated_accounts_count += 1 # Increment update/skipped count
                        # Optionally log the PL section being set/confirmed
                        # self.stdout.write(f"    [ACCOUNT] Processed {account_code_clean} (PL Section: {final_pl_section_member.name})")

                except IntegrityError as e:
                     self.stderr.write(self.style.ERROR(f"  [ERROR][Integrity] Account {account_code_clean}: {e}"))
                     updated_accounts_count -= 1 # Decrement if errored
                except ValidationError as e:
                     self.stderr.write(self.style.ERROR(f"  [ERROR][Validation] Account {account_code_clean}: {e.message_dict if hasattr(e, 'message_dict') else e}"))
                     updated_accounts_count -= 1 # Decrement if errored
                except Exception as e:
                     self.stderr.write(self.style.ERROR(f"  [ERROR][General] Account {account_code_clean}: {e}"))
                     updated_accounts_count -= 1 # Decrement if errored


        # --- Final Summary ---
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS('--- Chart of Accounts Seeding/Update Complete ---'))
        self.stdout.write(f'  Groups Created: {created_groups_count}')
        # self.stdout.write(f'  Groups Updated: {updated_groups_count}') # update_or_create doesn't tell us this easily
        self.stdout.write(f'  Groups Skipped/Found: {skipped_groups_count}')
        self.stdout.write(f'  Accounts Created: {created_accounts_count}')
        # self.stdout.write(f'  Accounts Updated: {updated_accounts_count}') # This count now includes skipped/found
        self.stdout.write(f'  Accounts Processed (Updated/Skipped/Found): {updated_accounts_count}')
        self.stdout.write(self.style.SUCCESS('---------------------------------------------------'))
# # crp_accounting/management/commands/seed_coa.py
#
# import logging
# from decimal import Decimal
# from django.core.management.base import BaseCommand, CommandError
# from django.db import transaction, IntegrityError, models
#
# # --- Project-Specific Imports ---
# try:
#     from crp_accounting.models import AccountGroup, Account # Import simplified models
#     from crp_core.constants import ACCOUNT_ROLE_GROUPS, ACCOUNT_NATURE
#     from crp_core.enums import AccountType, AccountNature, CurrencyType, PartyType
# except ImportError as e:
#     raise CommandError(f"Could not import necessary modules. Check paths and dependencies: {e}")
#
# logger = logging.getLogger(__name__)
#
# # --- Derived Mappings (Same as before) ---
# # Map constant group concepts to AccountType enum names
# GROUP_CONCEPT_TO_ACCOUNT_TYPE = {
#     'Assets': AccountType.ASSET.name,
#     'Liabilities': AccountType.LIABILITY.name,
#     'Equity': AccountType.EQUITY.name,
#     'Income': AccountType.INCOME.name,
#     'Cost of Goods Sold': AccountType.EXPENSE.name,
#     'Expenses': AccountType.EXPENSE.name,
#     'Non-Operational': AccountType.EXPENSE.name,
#     'Taxation': AccountType.LIABILITY.name,
#     'Receivables': AccountType.ASSET.name,
#     'Payables': AccountType.LIABILITY.name,
# }
# # Map AccountType names back to AccountNature names
# ACCOUNT_TYPE_TO_NATURE = {}
# if ACCOUNT_NATURE:
#     for concept, nature_str in ACCOUNT_NATURE.items():
#         ac_type_name = GROUP_CONCEPT_TO_ACCOUNT_TYPE.get(concept)
#         if ac_type_name:
#             nature_enum_member = next((member for name, member in AccountNature.__members__.items() if member.value.lower() == nature_str.lower()), None)
#             if nature_enum_member:
#                 if ac_type_name not in ACCOUNT_TYPE_TO_NATURE:
#                      ACCOUNT_TYPE_TO_NATURE[ac_type_name] = nature_enum_member.name
#             else:
#                  logger.warning(f"Nature value '{nature_str}' for concept '{concept}' in ACCOUNT_NATURE constant does not match any AccountNature enum value.")
# # Fallback natures
# fallback_natures = {
#     AccountType.ASSET.name: AccountNature.DEBIT.name, AccountType.LIABILITY.name: AccountNature.CREDIT.name,
#     AccountType.EQUITY.name: AccountNature.CREDIT.name, AccountType.INCOME.name: AccountNature.CREDIT.name,
#     AccountType.EXPENSE.name: AccountNature.DEBIT.name,
# }
# for acc_type, nature_name in fallback_natures.items():
#     if acc_type not in ACCOUNT_TYPE_TO_NATURE: ACCOUNT_TYPE_TO_NATURE[acc_type] = nature_name
# # Control Accounts Map
# CONTROL_ACCOUNTS_MAP = {
#     'asset_ca_recv': PartyType.CUSTOMER.name, 'rec_due_from_customers': PartyType.CUSTOMER.name,
#     'lia_cl_acc_payable': PartyType.SUPPLIER.name, 'pay_due_to_suppliers': PartyType.SUPPLIER.name,
# }
#
# class Command(BaseCommand):
#     """
#     Seeds/Updates the Chart of Accounts structure using standard ForeignKeys.
#     Reads config from constants.py, populates AccountGroup and Account models.
#     Idempotent using update_or_create.
#     """
#     help = 'Seeds or updates the database with the default Chart of Accounts structure (No MPTT).'
#
#     @transaction.atomic
#     def handle(self, *args, **options):
#         self.stdout.write(self.style.SUCCESS('--- Starting Chart of Accounts Seeding/Update (No MPTT) ---'))
#         # Counters
#         created_groups_count, updated_groups_count, skipped_groups_count = 0, 0, 0
#         created_accounts_count, updated_accounts_count, skipped_accounts_count = 0, 0, 0
#         # Cache
#         group_objects_map = {}
#
#         # 1. Ensure Primary Groups Exist or Update
#         primary_group_names = set()
#         for constant_group_key in ACCOUNT_ROLE_GROUPS.keys():
#             primary_name = constant_group_key.split(' - ')[0].split(' (')[0].split(' / ')[0].strip()
#             if primary_name: primary_group_names.add(primary_name)
#
#         self.stdout.write(f"Processing primary group concepts: {', '.join(sorted(primary_group_names))}")
#         for name in sorted(primary_group_names):
#             group_defaults = {'parent_group': None, 'is_primary': True} # Use parent_group
#             group, created = AccountGroup.objects.update_or_create(
#                 name=name, defaults=group_defaults
#             )
#             group_objects_map[name] = group
#             if created:
#                 created_groups_count += 1; self.stdout.write(f"  [GROUP] Created primary: '{name}'")
#             else: # Check if update occurred
#                 updated = False
#                 for key, value in group_defaults.items():
#                     if getattr(group, key) != value: setattr(group, key, value); updated = True
#                 if updated:
#                     try: group.save(); updated_groups_count += 1; self.stdout.write(f"  [GROUP] Updated existing primary: '{name}'")
#                     except Exception as e: self.stderr.write(self.style.ERROR(f"  [ERROR] Failed update primary group {name}: {e}"))
#                 else: skipped_groups_count += 1
#
#         # 2. Create/Update Sub-Groups and Accounts
#         for group_name_from_constant, roles in ACCOUNT_ROLE_GROUPS.items():
#             self.stdout.write(f"\nProcessing Group: '{group_name_from_constant}'")
#             primary_name = group_name_from_constant.split(' - ')[0].split(' (')[0].split(' / ')[0].strip()
#             parent_group_obj = group_objects_map.get(primary_name)
#             if not parent_group_obj:
#                 self.stderr.write(self.style.WARNING(f"  [WARN] Parent group '{primary_name}' not found. Skipping '{group_name_from_constant}'."))
#                 continue
#
#             # Get or Create the Sub-Group
#             if group_name_from_constant == primary_name:
#                 self.stdout.write(f"  Processing accounts directly under primary group '{primary_name}'.")
#                 sub_group_obj = parent_group_obj # Use primary directly
#             else:
#                 sub_group_defaults = {'parent_group': parent_group_obj, 'is_primary': False} # Use parent_group
#                 try:
#                     sub_group_obj, created = AccountGroup.objects.update_or_create(
#                         name=group_name_from_constant, defaults=sub_group_defaults
#                     )
#                     group_objects_map[group_name_from_constant] = sub_group_obj
#                     if created:
#                         created_groups_count += 1; self.stdout.write(f"  [GROUP] Created sub-group: '{group_name_from_constant}'")
#                     else: # Check if update occurred
#                         updated = False
#                         for key, value in sub_group_defaults.items():
#                              current_value = getattr(sub_group_obj, key)
#                              if isinstance(getattr(AccountGroup, key).field, models.ForeignKey):
#                                  if current_value != value: setattr(sub_group_obj, key, value); updated = True
#                              elif current_value != value: setattr(sub_group_obj, key, value); updated = True
#                         if updated:
#                             try: sub_group_obj.save(); updated_groups_count += 1; self.stdout.write(f"  [GROUP] Updated existing sub-group: '{group_name_from_constant}'")
#                             except Exception as e: self.stderr.write(self.style.ERROR(f"  [ERROR] Failed update sub-group {group_name_from_constant}: {e}"))
#                         else: skipped_groups_count += 1
#                 except Exception as e:
#                      self.stderr.write(self.style.ERROR(f"  [ERROR] Failed create/update sub-group '{group_name_from_constant}': {e}. Skipping accounts."))
#                      continue
#
#             # Determine Account Type & Nature
#             account_type_name = GROUP_CONCEPT_TO_ACCOUNT_TYPE.get(primary_name)
#             account_nature_name = ACCOUNT_TYPE_TO_NATURE.get(account_type_name)
#             if not account_type_name or not account_nature_name:
#                  self.stderr.write(self.style.WARNING(f"  [WARN] Cannot determine Type/Nature for '{primary_name}'. Skipping accounts in '{group_name_from_constant}'."))
#                  continue
#
#             # Create/Update Accounts
#             for account_code, account_name in roles:
#                 account_code = account_code.strip(); account_name = account_name.strip()
#                 is_control = account_code in CONTROL_ACCOUNTS_MAP
#                 control_party_type = CONTROL_ACCOUNTS_MAP.get(account_code)
#                 account_defaults = {
#                     'account_name': account_name, 'account_group': sub_group_obj,
#                     'account_type': account_type_name, 'account_nature': account_nature_name,
#                     'currency': CurrencyType.INR.name, 'description': '',
#                     'allow_direct_posting': True, 'is_active': True,
#                     'is_control_account': is_control, 'control_account_party_type': control_party_type,
#                 }
#                 try:
#                     account_obj, created = Account.objects.update_or_create(account_number=account_code, defaults=account_defaults)
#                     if created: created_accounts_count += 1 # self.stdout.write(f"    [ACC] Created: {account_name} ({account_code})")
#                     else: # Check update
#                         updated = False
#                         for field in account_defaults.keys():
#                              current_value = getattr(account_obj, field)
#                              new_value = account_defaults[field]
#                              if isinstance(getattr(Account, field).field, models.ForeignKey):
#                                  current_pk = getattr(account_obj, f"{field}_id"); new_pk = new_value.pk if new_value else None
#                                  if current_pk != new_pk: setattr(account_obj, field, new_value); updated = True
#                              elif current_value != new_value: setattr(account_obj, field, new_value); updated = True
#                         if updated:
#                             try: account_obj.account_nature = account_defaults['account_nature']; account_obj.save(); updated_accounts_count += 1 # self.stdout.write(f"    [ACC] Updated: {account_name} ({account_code})")
#                             except Exception as e: self.stderr.write(self.style.ERROR(f"    [ERROR] Failed update account {account_code}: {e}"))
#                         else: skipped_accounts_count += 1
#                 except Exception as e: self.stderr.write(self.style.ERROR(f"  [ERROR] Failed create/update account {account_code} - {account_name}: {e}"))
#
#         # --- No MPTT Rebuild Needed ---
#
#         self.stdout.write(self.style.SUCCESS('\n--- Chart of Accounts Seeding/Update Complete (No MPTT) ---'))
#         # ... (print summary counters) ...
#         self.stdout.write(f'  Groups Created: {created_groups_count}, Updated: {updated_groups_count}, Skipped: {skipped_groups_count}')
#         self.stdout.write(f'  Accounts Created: {created_accounts_count}, Updated: {updated_accounts_count}, Skipped: {skipped_accounts_count}')
#         self.stdout.write(self.style.SUCCESS('---------------------------------------------------------'))
# # # crp_accounting/management/commands/seed_coa.py
# #
# # import logging
# # from django.core.management.base import BaseCommand, CommandError
# # from django.db import transaction, IntegrityError, models
# #
# #
# # # --- Project-Specific Imports ---
# # # Adjust these paths based on your actual project structure
# # try:
# #     from crp_accounting.models.coa import AccountGroup, Account
# #     from crp_core.constants import ACCOUNT_NATURE, ACCOUNT_ROLE_GROUPS
# #     from crp_core.enums import AccountType, AccountNature, PartyType, CurrencyType
# # except ImportError as e:
# #     raise CommandError(f"Could not import necessary modules. Check paths and dependencies: {e}")
# #
# # logger = logging.getLogger(__name__)
# #
# # # --- Derived Mappings (Essential for Translation) ---
# #
# # # Map constant group concepts (derived from ACCOUNT_ROLE_GROUPS keys) to AccountType enum names
# # GROUP_CONCEPT_TO_ACCOUNT_TYPE = {
# #     'Assets': AccountType.ASSET.name,
# #     'Liabilities': AccountType.LIABILITY.name,
# #     'Equity': AccountType.EQUITY.name,
# #     'Income': AccountType.INCOME.name,
# #     'Cost of Goods Sold (COGS)': AccountType.EXPENSE.name,
# #     'Expenses': AccountType.EXPENSE.name,
# #     'Non-Operational / Adjustments': AccountType.EXPENSE.name,
# #     'Taxation': AccountType.LIABILITY.name,
# #     'Receivables': AccountType.ASSET.name,
# #     'Payables': AccountType.LIABILITY.name,
# # }
# #
# # # Map AccountType names back to AccountNature names using the ACCOUNT_NATURE constant
# # ACCOUNT_TYPE_TO_NATURE = {}
# # for concept, ac_type_name in GROUP_CONCEPT_TO_ACCOUNT_TYPE.items():
# #     nature_from_constant = ACCOUNT_NATURE.get(concept)
# #     if nature_from_constant:
# #         nature_name = next((n.name for n in AccountNature if n.value.lower() == nature_from_constant.lower()), None)
# #         if nature_name:
# #             ACCOUNT_TYPE_TO_NATURE[ac_type_name] = nature_name
# #         else:
# #              logger.warning(f"Nature value '{nature_from_constant}' for concept '{concept}' in ACCOUNT_NATURE constant does not match AccountNature enum values.")
# #     else:
# #         logger.warning(f"Concept '{concept}' mapped to AccountType '{ac_type_name}' but has no defined nature in ACCOUNT_NATURE constant.")
# #
# # # Ensure essential types have a fallback nature if not derived
# # if AccountType.EXPENSE.name not in ACCOUNT_TYPE_TO_NATURE:
# #     ACCOUNT_TYPE_TO_NATURE[AccountType.EXPENSE.name] = AccountNature.DEBIT.name
# #     logger.warning(f"Setting default nature '{AccountNature.DEBIT.name}' for AccountType '{AccountType.EXPENSE.name}' as it wasn't derived from constants.")
# #
# #
# # # Identify specific control accounts by their code from ACCOUNT_ROLE_GROUPS keys
# # CONTROL_ACCOUNTS_MAP = {
# #     'asset_ca_recv': PartyType.CUSTOMER.name,
# #     'rec_due_from_customers': PartyType.CUSTOMER.name,
# #     'lia_cl_acc_payable': PartyType.SUPPLIER.name,
# #     'pay_due_to_suppliers': PartyType.SUPPLIER.name,
# #     # Add other control accounts here if needed
# # }
# #
# #
# # class Command(BaseCommand):
# #     """
# #     Seeds the database with a default Chart of Accounts (COA) structure.
# #
# #     Reads configuration from `crp_core.constants` and populates the
# #     `AccountGroup` and `Account` models. Ensures a standard, hierarchical
# #     COA based on common accounting principles is available.
# #
# #     This command is idempotent: it creates missing entries and updates existing
# #     ones based on the constants file if re-run.
# #     """
# #     help = 'Seeds or updates the database with the default Chart of Accounts structure.'
# #
# #     @transaction.atomic # Ensures the entire seeding process succeeds or fails together
# #     def handle(self, *args, **options):
# #         self.stdout.write(self.style.SUCCESS('--- Starting Chart of Accounts Seeding/Update ---'))
# #
# #         created_groups_count = 0
# #         created_accounts_count = 0
# #         updated_groups_count = 0 # Track updates
# #         updated_accounts_count = 0 # Track updates
# #         skipped_groups_count = 0 # Track unchanged existing groups
# #         skipped_accounts_count = 0 # Track unchanged existing accounts
# #
# #         # Cache for created/retrieved group objects to efficiently link parents/children
# #         group_objects_cache = {}
# #
# #         # 1. Ensure Primary Groups Exist or Update
# #         primary_group_names = set()
# #         for constant_group_key in ACCOUNT_ROLE_GROUPS.keys():
# #             primary_name = constant_group_key.split(' - ')[0].split(' (')[0].split(' / ')[0].strip()
# #             if primary_name:
# #                 primary_group_names.add(primary_name)
# #
# #         self.stdout.write(f"Processing primary group concepts: {', '.join(sorted(primary_group_names))}")
# #         for name in sorted(primary_group_names):
# #             group_defaults = {'parent_group': None, 'is_primary': True}
# #             group, created = AccountGroup.objects.get_or_create(
# #                 name=name,
# #                 defaults=group_defaults
# #             )
# #             group_objects_cache[name] = group
# #             if created:
# #                 created_groups_count += 1
# #                 self.stdout.write(f"  [GROUP] Created primary: '{name}'")
# #             else:
# #                 # Check if existing primary group needs update (e.g., is_primary flag)
# #                 updated = False
# #                 for key, value in group_defaults.items():
# #                      # Intentionally skip parent_group check for primary as it should be None
# #                      if key != 'parent_group' and getattr(group, key) != value:
# #                          setattr(group, key, value)
# #                          updated = True
# #                 if updated:
# #                     try:
# #                         group.save()
# #                         updated_groups_count += 1
# #                         self.stdout.write(f"  [GROUP] Updated existing primary: '{name}'")
# #                     except Exception as e:
# #                         self.stderr.write(self.style.ERROR(f"  [ERROR] Failed to update primary group {name}: {e}"))
# #                 else:
# #                     skipped_groups_count += 1 # No change needed
# #
# #
# #         # 2. Create or Update Sub-Groups and Accounts from ACCOUNT_ROLE_GROUPS Constant
# #         for group_name_from_constant, roles in ACCOUNT_ROLE_GROUPS.items():
# #             self.stdout.write(f"\nProcessing Group: '{group_name_from_constant}'")
# #
# #             # --- Determine Parent Group ---
# #             primary_name = group_name_from_constant.split(' - ')[0].split(' (')[0].split(' / ')[0].strip()
# #             parent_group_obj = group_objects_cache.get(primary_name)
# #             if not parent_group_obj:
# #                 self.stderr.write(self.style.WARNING(f"  [WARN] Parent group '{primary_name}' not found for '{group_name_from_constant}'. Skipping."))
# #                 continue
# #
# #             # --- Create or Update Sub-Group ---
# #             sub_group_defaults = {'parent_group': parent_group_obj, 'is_primary': False}
# #             sub_group_obj, created = AccountGroup.objects.get_or_create(
# #                 name=group_name_from_constant,
# #                 defaults=sub_group_defaults
# #             )
# #             group_objects_cache[group_name_from_constant] = sub_group_obj
# #             if created:
# #                 created_groups_count += 1
# #                 self.stdout.write(f"  [GROUP] Created sub-group: '{group_name_from_constant}'")
# #             else:
# #                 # Check if existing sub-group needs update (e.g., parent changed)
# #                 updated = False
# #                 for key, value in sub_group_defaults.items():
# #                     if getattr(sub_group_obj, key) != value:
# #                         setattr(sub_group_obj, key, value)
# #                         updated = True
# #                 if updated:
# #                      try:
# #                         sub_group_obj.save()
# #                         updated_groups_count += 1
# #                         self.stdout.write(f"  [GROUP] Updated existing sub-group: '{group_name_from_constant}'")
# #                      except Exception as e:
# #                          self.stderr.write(self.style.ERROR(f"  [ERROR] Failed to update sub-group {group_name_from_constant}: {e}"))
# #                 else:
# #                     skipped_groups_count += 1 # No change needed
# #
# #             # --- Determine Account Type & Nature ---
# #             account_type_name = GROUP_CONCEPT_TO_ACCOUNT_TYPE.get(primary_name)
# #             account_nature_name = ACCOUNT_TYPE_TO_NATURE.get(account_type_name)
# #             if not account_type_name or not account_nature_name:
# #                  self.stderr.write(self.style.WARNING(f"  [WARN] Cannot determine Type/Nature for concept '{primary_name}'. Skipping accounts in '{group_name_from_constant}'."))
# #                  continue
# #
# #             # --- Create or Update Individual Accounts (Ledgers) ---
# #             for account_code, account_name in roles:
# #                 is_control = account_code in CONTROL_ACCOUNTS_MAP
# #                 control_party_type = CONTROL_ACCOUNTS_MAP.get(account_code)
# #
# #                 # Define the desired state based on constants
# #                 account_defaults = {
# #                     'account_name': account_name.strip(),
# #                     'account_group': sub_group_obj,
# #                     'account_type': account_type_name,
# #                     'account_nature': account_nature_name,
# #                     'currency': CurrencyType.INR.name, # Default currency
# #                     'allow_direct_posting': True,
# #                     'is_active': True,
# #                     'is_control_account': is_control,
# #                     'control_account_party_type': control_party_type,
# #                 }
# #
# #                 # Create or get the account
# #                 account_obj, created = Account.objects.get_or_create(
# #                     account_number=account_code.strip(),
# #                     defaults=account_defaults
# #                 )
# #
# #                 if created:
# #                     created_accounts_count += 1
# #                     self.stdout.write(f"    [ACCOUNT] Created: {account_name} ({account_code})")
# #                 else:
# #                     # If account already existed, check if it needs updating
# #                     update_needed = False
# #                     fields_to_compare = [
# #                         'account_name', 'account_group', 'account_type', 'account_nature',
# #                         'currency', 'allow_direct_posting', 'is_active',
# #                         'is_control_account', 'control_account_party_type'
# #                     ]
# #                     for field in fields_to_compare:
# #                         current_value = getattr(account_obj, field)
# #                         new_value = account_defaults[field]
# #                         # Handle FK comparison by comparing PKs or objects directly if cached
# #                         if isinstance(getattr(Account, field).field, models.ForeignKey):
# #                             if current_value != new_value: # Compares FK objects
# #                                 setattr(account_obj, field, new_value)
# #                                 update_needed = True
# #                         elif current_value != new_value:
# #                             setattr(account_obj, field, new_value)
# #                             update_needed = True
# #
# #                     if update_needed:
# #                         try:
# #                             # Must explicitly save derived fields too if they could change
# #                             account_obj.account_nature = account_defaults['account_nature']
# #                             account_obj.save() # Save the changes
# #                             updated_accounts_count += 1
# #                             self.stdout.write(f"    [ACCOUNT] Updated existing: {account_name} ({account_code})")
# #                         except (IntegrityError, Exception) as e:
# #                             self.stderr.write(self.style.ERROR(f"    [ERROR] Failed to update account {account_code}: {e}"))
# #                     else:
# #                         skipped_accounts_count += 1 # No change needed
# #
# #
# #         self.stdout.write(self.style.SUCCESS('\n--- Chart of Accounts Seeding/Update Complete ---'))
# #         self.stdout.write(f'  Groups Created: {created_groups_count}')
# #         self.stdout.write(f'  Groups Updated: {updated_groups_count}')
# #         self.stdout.write(f'  Groups Skipped (Unchanged): {skipped_groups_count}')
# #         self.stdout.write(f'  Accounts Created: {created_accounts_count}')
# #         self.stdout.write(f'  Accounts Updated: {updated_accounts_count}')
# #         self.stdout.write(f'  Accounts Skipped (Unchanged): {skipped_accounts_count}')
# #         self.stdout.write(self.style.SUCCESS('------------------------------------------------'))