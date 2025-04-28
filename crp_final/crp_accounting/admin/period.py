from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from crp_accounting.models.period import FiscalYear, AccountingPeriod


@admin.register(FiscalYear)
class FiscalYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "status", "is_active", "closed_at", "closed_by")
    list_filter = ("status", "is_active")
    search_fields = ("name",)
    readonly_fields = ("closed_by", "closed_at", "created_at", "updated_at")

    actions = ["activate_fiscal_year", "close_fiscal_year"]

    def activate_fiscal_year(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, _("Please select only one fiscal year to activate."), level=messages.WARNING)
            return

        year = queryset.first()
        year.activate()
        self.message_user(request, _(f"Fiscal Year '{year.name}' has been activated."), level=messages.SUCCESS)

    activate_fiscal_year.short_description = "Activate selected fiscal year"

    def close_fiscal_year(self, request, queryset):
        for year in queryset:
            if year.status == "Closed":
                self.message_user(request, _(f"Fiscal Year '{year.name}' is already closed."), level=messages.WARNING)
                continue

            year.close_year(user=request.user)
            self.message_user(request, _(f"Fiscal Year '{year.name}' has been closed."), level=messages.SUCCESS)

    close_fiscal_year.short_description = "Close selected fiscal years"

@admin.register(AccountingPeriod)
class AccountingPeriodAdmin(admin.ModelAdmin):
    list_display = ("fiscal_year", "start_date", "end_date", "locked", "lock_unlock_action")
    list_filter = ("fiscal_year", "locked")
    search_fields = ("fiscal_year__name", "start_date", "end_date")  # 👈 this line is crucial

    actions = ["lock_selected_periods", "unlock_selected_periods"]

    def lock_unlock_action(self, obj):
        if obj.locked:
            return format_html('<span style="color:red;">🔒 Locked</span>')
        return format_html('<span style="color:green;">🔓 Open</span>')

    lock_unlock_action.short_description = "Status"

    def lock_selected_periods(self, request, queryset):
        updated = 0
        for period in queryset:
            if not period.locked:
                period.lock()
                updated += 1
        self.message_user(request, _(f"{updated} accounting period(s) locked."), level=messages.SUCCESS)

    def unlock_selected_periods(self, request, queryset):
        updated = 0
        for period in queryset:
            if period.locked:
                period.unlock()
                updated += 1
        self.message_user(request, _(f"{updated} accounting period(s) unlocked."), level=messages.SUCCESS)

    actions = ["lock_selected_periods", "unlock_selected_periods"]

    def lock_unlock_action(self, obj):
        if obj.locked:
            return format_html('<span style="color:red;">🔒 Locked</span>')
        return format_html('<span style="color:green;">🔓 Open</span>')

    lock_unlock_action.short_description = "Status"

    def lock_selected_periods(self, request, queryset):
        updated = 0
        for period in queryset:
            if not period.locked:
                period.lock()
                updated += 1
        self.message_user(request, _(f"{updated} accounting period(s) locked."), level=messages.SUCCESS)

    lock_selected_periods.short_description = "Lock selected accounting periods"

    def unlock_selected_periods(self, request, queryset):
        updated = 0
        for period in queryset:
            if period.locked:
                period.unlock()
                updated += 1
        self.message_user(request, _(f"{updated} accounting period(s) unlocked."), level=messages.SUCCESS)

    unlock_selected_periods.short_description = "Unlock selected accounting periods"
