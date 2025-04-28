# crp_accounting/admin.py

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

# Import models from coa.py
from ..models.coa import AccountGroup, Account
# --- Import the new PLSection enum ---
from ..models.coa import PLSection

#------------------------------------------------------------------------------
# Inline Admin: Account inside AccountGroup (No change needed for pl_section)
#------------------------------------------------------------------------------

class AccountInline(admin.TabularInline):
    """
    Inline admin for Accounts within AccountGroup.
    Allows quick access/editing of accounts from the group detail page.
    """
    model = Account
    fields = (
        'account_number',
        'account_name',
        'account_type',
        # 'pl_section', # Optional: Add here if critical to see inline, otherwise keep it concise
        'is_active',
        'allow_direct_posting',
        'current_balance',
    )
    # Nature and balance are system-managed
    readonly_fields = ('account_nature', 'current_balance',)
    extra = 0 # Don't show extra blank rows by default
    show_change_link = True # Allow clicking through to the full Account admin
    ordering = ('account_number',)

    # Optional: Limit which fields are editable inline if needed
    # def get_readonly_fields(self, request, obj=None): ...


#------------------------------------------------------------------------------
# Admin: AccountGroup (No change needed for pl_section)
#------------------------------------------------------------------------------

@admin.register(AccountGroup)
class AccountGroupAdmin(admin.ModelAdmin):
    """
    Admin interface for AccountGroup, supporting inline display of Accounts
    and hierarchical parent group navigation.
    """
    list_display = (
        'display_name_with_indent', # Hierarchical display
        'parent_group_link', # Link to parent
    )
    list_filter = ('parent_group',) # Filter by parent
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['parent_group'] # Autocomplete for parent selection
    inlines = [AccountInline] # Show related accounts inline

    fieldsets = (
        (None, {
            'fields': ('name', 'parent_group', 'description'),
        }),
        (_('Audit Information'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',), # Keep collapsed
        }),
    )

    def get_queryset(self, request):
        """Optimize queries by prefetching related parent_group."""
        return super().get_queryset(request).select_related('parent_group').order_by('name')

    def get_level(self, obj):
        """Recursively calculate the nesting level of the group."""
        level = 0
        parent = obj.parent_group
        while parent:
            level += 1
            parent = parent.parent_group
        return level

    @admin.display(description=_('Group Name (Hierarchy)'), ordering='name')
    def display_name_with_indent(self, obj):
        """Display group name with visual indentation for hierarchy."""
        level = self.get_level(obj)
        indent = '    ' * level # Using non-breaking spaces
        return format_html('{}{}', format_html(indent), obj.name)

    @admin.display(description=_('Parent Group'), ordering='parent_group__name')
    def parent_group_link(self, obj):
        """Display parent group name as a link."""
        if obj.parent_group:
            from django.urls import reverse
            url = reverse('admin:crp_accounting_accountgroup_change', args=[obj.parent_group.pk])
            return format_html('<a href="{}">{}</a>', url, obj.parent_group.name)
        return '-'


#------------------------------------------------------------------------------
# Admin: Account (MODIFIED for pl_section)
#------------------------------------------------------------------------------

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """
    Admin interface for Account model, including balance display, P&L section, and filters.
    """
    list_display = (
        'id',
        'account_number',
        'account_name',
        'account_group',
        'account_type',
        'pl_section',
        'account_nature',
        'current_balance',
        'currency',
        'is_active',
        'allow_direct_posting',
        'is_control_account',
        'balance_last_updated',
    )
    list_filter = (
        'is_active',
        'allow_direct_posting',
        'account_type',
        'pl_section',           # <<< ADDED pl_section to filters
        'account_nature',
        'is_control_account',
        'control_account_party_type',
        'currency',
        'account_group',
    )
    search_fields = (
        'account_number',
        'account_name',
        'description',
        'account_group__name',
        # 'pl_section', # Searching by exact enum value might not be very useful
    )
    # Fields that are system-managed or calculated
    readonly_fields = (
        'account_nature',
        'current_balance',
        'balance_last_updated',
        'created_at',
        'updated_at',
    )
    autocomplete_fields = ['account_group']
    list_select_related = ('account_group',)
    actions = ['make_active', 'make_inactive']
    list_per_page = 25

    fieldsets = (
        (None, { # Main identification
            'fields': ('account_number', 'account_name', 'account_group', 'description'),
        }),
        (_('Classification & Nature'), { # Classification details
            'fields': (
                'account_type',
                'pl_section',       # <<< ADDED pl_section to fieldset
                'account_nature',
                'currency'
            ),
        }),
        (_('Posting & Control Settings'), { # How the account behaves
            'fields': ('is_active', 'allow_direct_posting', 'is_control_account', 'control_account_party_type'),
        }),
        (_('Balance Information'), { # Display balance details
            'fields': ('current_balance', 'balance_last_updated'),
        }),
        (_('Audit Information'), { # Standard audit fields
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        """Prevent editing account_number after creation."""
        ro_fields = list(self.readonly_fields)
        if obj:
            ro_fields.append('account_number')
        return tuple(ro_fields)

    # --- Admin Actions ---
    @admin.action(description=_('Mark selected accounts as active'))
    def make_active(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} account(s) marked as active.")

    @admin.action(description=_('Mark selected accounts as inactive'))
    def make_inactive(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} account(s) marked as inactive.")