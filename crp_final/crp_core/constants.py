# core/constants.py

"""
Contains static constants like ACCOUNT_ROLE_GROUPS used for seeding
a comprehensive Chart of Accounts (COA) structure, reflecting common
accounting standards and detail found in systems like Tally.
"""

# =============================================================================
# Comprehensive Chart of Accounts Structure Definition
# =============================================================================
# Note:
# - Codes ('1000_cash') are examples; adjust numbering as needed. Must be unique.
# - The seeding script (migration/command) will use this structure to create
#   AccountGroup and Account records, assigning AccountType based on the top-level group.
# - Some accounts listed might function as 'summary' or 'group' accounts in practice,
#   meaning 'allow_direct_posting' should be set to False during seeding for those.
#   We mark potential summary accounts with a comment # Summary?

ACCOUNT_ROLE_GROUPS = {

    # ========================== ASSETS ==========================
    'Assets - Current Assets': [
        # --- Cash & Bank ---
        ('1000_cash', 'Cash on Hand / Petty Cash'),
        ('1010_bank_accounts', 'Bank Accounts'), # Summary? Or allow posting if only one main bank
        ('1011_bank_account_checking', 'Bank Account - Checking'),
        ('1012_bank_account_savings', 'Bank Account - Savings'),
        ('1015_undeposited_funds', 'Undeposited Funds'), # Funds received but not yet banked
        ('1020_marketable_securities', 'Marketable Securities - Short Term'),
        # --- Receivables ---
        ('1030_accounts_receivable', 'Accounts Receivable - Trade'), # Summary? Often a Control Account
        ('1031_trade_debtors', 'Trade Debtors Control'), # Explicit Control Account
        ('1035_notes_receivable_current', 'Notes Receivable - Current'),
        ('1036_interest_receivable', 'Interest Receivable'),
        ('1039_allowance_for_doubtful_accounts', 'Allowance for Doubtful Accounts'), # Contra Asset
        ('1040_advances_to_suppliers', 'Advances to Suppliers'),
        ('1045_employee_advances', 'Employee Advances'),
        # --- Inventory ---
        ('1050_inventory', 'Inventory'), # Summary?
        ('1051_inventory_raw_materials', 'Inventory - Raw Materials'),
        ('1052_inventory_work_in_progress', 'Inventory - Work In Progress'),
        ('1053_inventory_finished_goods', 'Inventory - Finished Goods'),
        ('1054_inventory_merchandise', 'Inventory - Merchandise'),
        # --- Prepaid Expenses ---
        ('1070_prepaid_expenses', 'Prepaid Expenses'), # Summary?
        ('1071_prepaid_rent', 'Prepaid Rent'),
        ('1072_prepaid_insurance', 'Prepaid Insurance'),
        ('1073_prepaid_advertising', 'Prepaid Advertising'),
        # --- Other Current Assets ---
        ('1080_accrued_income', 'Accrued Income'),
        ('1090_other_current_assets', 'Other Current Assets'),
        ('1100_short_term_investments', 'Short-Term Investments'),
        ('1110_tax_recoverable_gst', 'Tax Recoverable - GST/VAT'),
        ('1111_tax_recoverable_income', 'Tax Recoverable - Income Tax'),
    ],
    'Assets - Non-Current Assets': [
        # --- Long-Term Investments ---
        ('1200_notes_receivable_long_term', 'Notes Receivable - Long-Term'),
        ('1220_long_term_securities', 'Long-Term Securities'),
        ('1230_investments_in_subsidiaries', 'Investments in Subsidiaries / Associates'),
        ('1240_other_long_term_investments', 'Other Long-Term Investments'),
        # --- Property, Plant, Equipment (Fixed Assets) ---
        ('1300_land', 'Land'), # Not depreciated
        ('1305_land_improvements', 'Land Improvements'),
        ('1306_land_improvements_accum_depr', 'Land Improvements - Accum. Depreciation'), # Contra Asset
        ('1310_buildings', 'Buildings'),
        ('1311_buildings_accum_depr', 'Buildings - Accum. Depreciation'), # Contra Asset
        ('1320_plant_and_machinery', 'Plant and Machinery'),
        ('1321_plant_and_machinery_accum_depr', 'Plant and Machinery - Accum. Depreciation'), # Contra Asset
        ('1330_furniture_and_fixtures', 'Furniture and Fixtures'),
        ('1331_furniture_fixtures_accum_depr', 'Furniture & Fixtures - Accum. Depreciation'), # Contra Asset
        ('1340_vehicles', 'Vehicles'),
        ('1341_vehicles_accum_depr', 'Vehicles - Accum. Depreciation'), # Contra Asset
        ('1350_office_equipment', 'Office Equipment'),
        ('1351_office_equipment_accum_depr', 'Office Equipment - Accum. Depreciation'), # Contra Asset
        ('1360_computer_hardware', 'Computer Hardware'),
        ('1361_computer_hardware_accum_depr', 'Computer Hardware - Accum. Depreciation'), # Contra Asset
        ('1370_leased_assets', 'Leased Assets (Finance Lease)'),
        ('1371_leased_assets_accum_depr', 'Leased Assets - Accum. Depreciation'), # Contra Asset
        ('1380_construction_in_progress', 'Construction in Progress (Capital WIP)'),
        # --- Intangible Assets ---
        ('1400_intangible_assets', 'Intangible Assets'), # Summary?
        ('1410_goodwill', 'Goodwill'), # Not amortized under IFRS/GAAP usually, tested for impairment
        ('1420_patents', 'Patents'),
        ('1421_patents_accum_amort', 'Patents - Accum. Amortization'), # Contra Asset
        ('1430_trademarks', 'Trademarks'),
        ('1431_trademarks_accum_amort', 'Trademarks - Accum. Amortization'), # Contra Asset
        ('1440_licenses_and_franchises', 'Licenses and Franchises'),
        ('1441_licenses_franchises_accum_amort', 'Licenses & Franchises - Accum. Amortization'), # Contra Asset
        ('1450_software_developed_purchased', 'Software (Developed/Purchased)'),
        ('1451_software_accum_amort', 'Software - Accum. Amortization'), # Contra Asset
        ('1460_other_intangible_assets', 'Other Intangible Assets'),
        ('1461_other_intangibles_accum_amort', 'Other Intangibles - Accum. Amortization'), # Contra Asset
        # --- Other Non-Current Assets ---
        ('1500_deferred_tax_assets', 'Deferred Tax Assets'),
        ('1510_security_deposits_long_term', 'Security Deposits - Long Term'),
        ('1520_other_non_current_assets', 'Other Non-Current Assets'),
    ],

    # ======================= LIABILITIES ========================
    'Liabilities - Current Liabilities': [
        # --- Payables ---
        ('2000_accounts_payable', 'Accounts Payable - Trade'), # Summary? Often a Control Account
        ('2001_trade_creditors', 'Trade Creditors Control'), # Explicit Control Account
        ('2005_notes_payable_current', 'Notes Payable - Current'),
        ('2006_interest_payable', 'Interest Payable'),
        # --- Accrued Expenses ---
        ('2010_salaries_and_wages_payable', 'Salaries and Wages Payable'),
        ('2015_payroll_taxes_payable', 'Payroll Taxes Payable'),
        ('2020_income_taxes_payable', 'Income Taxes Payable'),
        ('2021_sales_tax_payable_gst_vat', 'Sales Tax Payable (GST/VAT)'),
        ('2022_other_taxes_payable', 'Other Taxes Payable'),
        ('2030_accrued_rent', 'Accrued Rent'),
        ('2031_accrued_utilities', 'Accrued Utilities'),
        ('2035_other_accrued_expenses', 'Other Accrued Expenses'),
        # --- Other Current Liabilities ---
        ('2040_current_portion_long_term_debt', 'Current Portion of Long-Term Debt'),
        ('2050_deferred_revenue_current', 'Deferred Revenue - Current'),
        ('2060_customer_advances_deposits', 'Customer Advances / Deposits'),
        ('2070_dividends_payable', 'Dividends Payable'),
        ('2090_other_current_liabilities', 'Other Current Liabilities'),
    ],
    'Liabilities - Non-Current Liabilities': [
        ('2200_notes_payable_long_term', 'Notes Payable - Long-Term'),
        ('2210_bonds_payable', 'Bonds Payable'),
        ('2220_mortgage_payable', 'Mortgage Payable'),
        ('2230_lease_liabilities_long_term', 'Lease Liabilities - Long-Term'),
        ('2240_deferred_tax_liabilities', 'Deferred Tax Liabilities'),
        ('2250_pension_benefit_obligations', 'Pension Benefit Obligations'),
        ('2260_provisions_long_term', 'Provisions - Long-Term'),
        ('2270_other_long_term_liabilities', 'Other Long-Term Liabilities'),
    ],

    # ========================== EQUITY ==========================
    'Equity': [
        # --- Contributed Capital ---
        ('3000_common_stock', 'Common Stock / Ordinary Shares'),
        ('3010_preferred_stock', 'Preferred Stock'),
        ('3020_additional_paid_in_capital', 'Additional Paid-in Capital / Share Premium'),
        # --- Owner Specific (Sole Prop/Partnership) ---
        ('3100_owner_capital', 'Owner’s Capital'),
        ('3110_owner_drawings', 'Owner’s Drawings'), # Contra Equity
        # --- Retained Earnings ---
        ('3200_retained_earnings', 'Retained Earnings (Accumulated Profit/Loss)'),
        ('3210_dividends_declared', 'Dividends Declared'), # Often closed to Retained Earnings
        # --- Other Equity Components ---
        ('3300_treasury_stock', 'Treasury Stock'), # Contra Equity
        ('3400_accumulated_other_comprehensive_income', 'Accumulated Other Comprehensive Income (AOCI)'),
        ('3410_foreign_currency_translation_adj', 'Foreign Currency Translation Adjustment (AOCI)'),
        ('3420_unrealized_gain_loss_securities_oci', 'Unrealized Gain/Loss on Securities (AOCI)'),
        ('3500_reserves', 'Reserves (Statutory, General, etc.)'),
    ],

    # ========================== INCOME / REVENUE ==========================
    'Income - Operating Revenue': [
        ('4000_sales_revenue', 'Sales Revenue'), # Summary?
        ('4010_sales_product_a', 'Sales - Product A'),
        ('4011_sales_product_b', 'Sales - Product B'),
        ('4020_service_revenue', 'Service Revenue'), # Summary?
        ('4021_service_consulting', 'Service Revenue - Consulting'),
        ('4022_service_maintenance', 'Service Revenue - Maintenance'),
        ('4050_sales_returns_allowances', 'Sales Returns and Allowances'), # Contra Revenue
        ('4060_sales_discounts', 'Sales Discounts'), # Contra Revenue
    ],
    'Income - Other Income': [
        ('4100_interest_income', 'Interest Income'),
        ('4110_dividend_income', 'Dividend Income'),
        ('4120_rental_income', 'Rental Income'),
        ('4130_gain_on_sale_of_assets', 'Gain on Sale of Assets'),
        ('4140_foreign_exchange_gain_operating', 'Foreign Exchange Gain - Operating'),
        ('4190_miscellaneous_income', 'Miscellaneous Income'),
    ],

    # =================== COST OF GOODS SOLD / COST OF SALES ===================
    'Cost of Goods Sold': [
        ('5000_cost_of_goods_sold', 'Cost of Goods Sold'), # Summary? Often calculated
        ('5010_purchases', 'Purchases'), # For periodic inventory system
        ('5011_purchase_returns_allowances', 'Purchase Returns and Allowances'), # Contra COGS/Purchase
        ('5012_purchase_discounts', 'Purchase Discounts'), # Contra COGS/Purchase
        ('5020_freight_in_shipping_costs', 'Freight-In / Shipping Costs'),
        ('5030_direct_labor_cogs', 'Direct Labor (COGS)'),
        ('5040_manufacturing_overhead_cogs', 'Manufacturing Overhead (COGS)'),
        ('5050_inventory_adjustments_cogs', 'Inventory Adjustments (COGS)'), # e.g., write-downs, obsolescence
        ('5060_cost_of_services', 'Cost of Services'), # For service companies
    ],

    # ========================= EXPENSES =========================
    'Expenses - Operating Expenses': [
        # --- Selling Expenses ---
        ('6000_selling_expenses', 'Selling Expenses'), # Summary?
        ('6010_sales_salaries_commissions', 'Sales Salaries and Commissions'),
        ('6020_advertising_expense', 'Advertising Expense'),
        ('6030_marketing_promotions', 'Marketing and Promotions'),
        ('6040_travel_entertainment_selling', 'Travel & Entertainment - Selling'),
        ('6050_freight_out_delivery_expense', 'Freight-Out / Delivery Expense'),
        # --- General & Administrative Expenses ---
        ('6100_general_administrative_expenses', 'General & Administrative Expenses'), # Summary?
        ('6110_office_salaries_wages', 'Office Salaries and Wages'),
        ('6115_employee_benefits_admin', 'Employee Benefits - Admin'),
        ('6120_rent_expense_office', 'Rent Expense - Office'),
        ('6130_office_supplies_expense', 'Office Supplies Expense'),
        ('6140_utilities_expense', 'Utilities Expense'),
        ('6141_utilities_electricity', 'Utilities - Electricity'),
        ('6142_utilities_water_sewage', 'Utilities - Water/Sewage'),
        ('6143_utilities_gas', 'Utilities - Gas'),
        ('6144_utilities_internet_phone', 'Utilities - Internet/Phone'),
        ('6150_insurance_expense_general', 'Insurance Expense - General'),
        ('6160_repairs_maintenance_office', 'Repairs and Maintenance - Office'),
        ('6170_depreciation_expense_admin', 'Depreciation Expense - Admin Assets'),
        ('6180_amortization_expense_admin', 'Amortization Expense - Admin Intangibles'),
        ('6190_property_taxes', 'Property Taxes'),
        ('6200_professional_fees', 'Professional Fees'), # Summary?
        ('6201_professional_fees_legal', 'Professional Fees - Legal'),
        ('6202_professional_fees_accounting', 'Professional Fees - Accounting/Audit'),
        ('6203_professional_fees_consulting', 'Professional Fees - Consulting'),
        ('6210_bank_charges_fees', 'Bank Charges and Fees'),
        ('6220_bad_debt_expense', 'Bad Debt Expense'),
        ('6230_travel_entertainment_admin', 'Travel & Entertainment - Admin'),
        ('6240_postage_courier', 'Postage and Courier'),
        ('6250_licenses_permits', 'Licenses and Permits'),
        ('6290_miscellaneous_admin_expense', 'Miscellaneous Admin Expense'),
        # --- Research & Development ---
        ('6300_research_development_expense', 'Research & Development Expense'),
    ],
    'Expenses - Other Expenses / Income': [
        ('6500_interest_expense', 'Interest Expense'),
        ('6510_loss_on_sale_of_assets', 'Loss on Sale of Assets'),
        ('6520_foreign_exchange_loss_non_operating', 'Foreign Exchange Loss - Non-Operating'),
        ('6530_income_tax_expense', 'Income Tax Expense'), # The expense itself, distinct from payable
        ('6590_other_non_operating_expense', 'Other Non-Operating Expense'),
    ],

    # === Optional: For internal tracking/grouping, might not be standard ===
    # 'Non-Operational / Adjustments': [ ... ],
    # 'Taxation': [ ... ], # Tax *Payable* accounts are under Liabilities
    # 'Receivables': [ ... ], # Receivable *Control* accounts are under Assets
    # 'Payables': [ ... ], # Payable *Control* accounts are under Liabilities
}


# =============================================================================
# Account Nature Mapping (Must align with TOP-LEVEL group keys above)
# =============================================================================
ACCOUNT_NATURE = {
    'Assets - Current Assets': 'DEBIT', # Changed to uppercase to match typical enum value storage
    'Assets - Non-Current Assets': 'DEBIT',
    'Liabilities - Current Liabilities': 'CREDIT',
    'Liabilities - Non-Current Liabilities': 'CREDIT',
    'Equity': 'CREDIT',
    'Income - Operating Revenue': 'CREDIT',
    'Income - Other Income': 'CREDIT',
    'Cost of Goods Sold': 'DEBIT',
    'Expenses - Operating Expenses': 'DEBIT',
    'Expenses - Other Expenses / Income': 'DEBIT', # Mostly expenses, so overall DEBIT nature
}
