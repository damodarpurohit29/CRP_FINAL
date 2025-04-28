# crp_accounting/views/profit_loss.py

import logging
from datetime import date

# Django & DRF Imports
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist # Potentially caught by central handler
from django.core.cache import cache # For manual caching logic
from django.conf import settings # To get cache timeout setting

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.exceptions import ParseError, ValidationError
# Optional: Import throttle classes only if overriding global settings per-view
# from rest_framework.throttling import UserRateThrottle, AnonRateThrottle

# --- Local Imports ---
# Adjust these paths based on your actual project structure
try:
    from ..services import reports_service
    from ..serializers.profit_loss import ProfitLossStructuredResponseSerializer
    from ..permissions import CanViewFinancialReports
except ImportError as e:
    # Raise configuration error early if imports fail
    raise ImportError(f"Could not import necessary modules for ProfitLossView. Check paths and dependencies: {e}")

# --- Swagger/Spectacular Imports ---
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiResponse

# --- Initialize Logger ---
logger = logging.getLogger(__name__)

# --- Cache Configuration ---
# Fetches 'PNL_REPORT_CACHE_TIMEOUT' from settings.py, defaults to 900 seconds (15 minutes)
# Recommendation: Keep this relatively short for financial reports unless data changes infrequently.
PNL_REPORT_CACHE_TIMEOUT = getattr(settings, 'PNL_REPORT_CACHE_TIMEOUT', 900)
# Define a clear prefix for P&L cache keys for better namespacing and potential management
PNL_CACHE_KEY_PREFIX = "crp_acct:pnl_report"

# =============================================================================
# Profit & Loss Report View
# =============================================================================

@extend_schema(
    summary="Generate Profit & Loss Report",
    description="""Generates a structured Profit and Loss (P&L) or Income Statement
report for a specified date range. Requires 'view_financial_reports' permission.

The report follows standard accounting principles, calculating Gross Profit and
Net Income, presenting data hierarchically within standard P&L sections.

**Caching:** Results for a given date range are cached server-side for performance
(typical duration: ~15 minutes, configured in settings). This uses a time-based
expiration strategy. Note that recently posted transactions might only appear
after the cache expires, as complex real-time invalidation is not implemented
by default. Cache size should also be monitored.

**Rate Limiting:** This endpoint is subject to global API rate limits defined
in `settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']` to prevent abuse.
    """,
    parameters=[
        OpenApiParameter(
            name='start_date',
            description='Required. Start date of the reporting period (YYYY-MM-DD).',
            required=True, type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY
        ),
        OpenApiParameter(
            name='end_date',
            description='Required. End date of the reporting period (YYYY-MM-DD).',
            required=True, type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY
        ),
    ],
    responses={
        200: ProfitLossStructuredResponseSerializer,
        400: OpenApiResponse(description="Bad Request - Missing/invalid parameters or date range."),
        403: OpenApiResponse(description="Forbidden - User lacks 'view_financial_reports' permission."),
        429: OpenApiResponse(description="Too Many Requests - Rate limit exceeded."),
        500: OpenApiResponse(description="Internal Server Error - Unexpected issue during report generation."),
    },
    tags=['Reports'] # Group this endpoint in the API documentation
)
class ProfitLossView(APIView):
    """
    API endpoint for the structured Profit & Loss Statement.

    Features:
    - Requires specific permissions ('view_financial_reports').
    - Implements time-based caching for performance.
    - Relies on globally configured DRF rate limiting.
    - Delegates report generation logic to the service layer.
    - Uses a dedicated serializer for response structure.
    - Assumes a centralized DRF exception handler is configured.

    *Cache Considerations:* Uses time-based expiration (`PNL_REPORT_CACHE_TIMEOUT`).
    Does not implement signal-based invalidation for simplicity. Monitor cache
    backend performance and memory usage for large/frequent reports.
    """
    # permission_classes = [CanViewFinancialReports]
    permission_classes = [permissions.IsAuthenticated]
    # --- Throttling Configuration ---
    # This view relies on DEFAULT_THROTTLE_CLASSES and DEFAULT_THROTTLE_RATES
    # defined in settings.py. Uncomment below ONLY if you need specific overrides
    # for this *particular* endpoint.
    # throttle_classes = [UserRateThrottle, AnonRateThrottle] # Example override
    # throttle_scope = 'reports' # Assigns a specific scope rate from settings

    def get(self, request, *args, **kwargs):
        """Handles GET requests to generate and return the Profit & Loss data."""

        # --- 1. Input Extraction and Validation ---
        # Get date parameters from the request query string.
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        logger.debug(f"P&L Request received for {start_date_str} to {end_date_str}")

        # Validate presence of required parameters.
        if not start_date_str:
            raise ParseError(detail=_("Query parameter 'start_date' (YYYY-MM-DD) is required."))
        if not end_date_str:
            raise ParseError(detail=_("Query parameter 'end_date' (YYYY-MM-DD) is required."))

        # Validate date format and logical range.
        try:
            start_date = date.fromisoformat(start_date_str)
            end_date = date.fromisoformat(end_date_str)
        except ValueError:
             raise ParseError(detail=_("Invalid date format. Use YYYY-MM-DD for 'start_date' and 'end_date'."))

        if start_date > end_date:
            raise ValidationError(detail=_("The 'start_date' cannot be after the 'end_date'."))
        logger.debug(f"P&L Request validated for date range: {start_date} to {end_date}")

        # --- 2. Caching - Attempt to Retrieve Cached Data ---
        # Construct a descriptive cache key including versioning (v1) if format changes.
        cache_key = f"{PNL_CACHE_KEY_PREFIX}:v1:{start_date.isoformat()}:{end_date.isoformat()}"
        cached_data = None # Initialize
        try:
            # Attempt to fetch data from the configured cache backend.
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                logger.info(f"P&L View: Cache HIT for key: {cache_key}")
                # Return the cached JSON response directly.
                return Response(cached_data, status=status.HTTP_200_OK)
            else:
                logger.info(f"P&L View: Cache MISS for key: {cache_key}. Proceeding to generate report.")
        except Exception as e:
            # Log errors during cache retrieval but don't fail the request.
            # Proceed as if it was a cache miss.
            logger.error(f"P&L View: Cache GET failed for key {cache_key}: {e}", exc_info=True)
            cached_data = None # Ensure it's treated as a miss

        # --- 3. Service Layer Call (Executed on Cache Miss) ---
        # Delegate the complex report generation logic to the service layer.
        logger.debug(f"P&L View: Calling reports_service for period {start_date} to {end_date}")
        try:
            report_data = reports_service.generate_profit_loss_structured(
                start_date=start_date,
                end_date=end_date
            )
        except Exception as e:
            # Log unexpected errors originating from the service layer.
            user_pk = request.user.pk if request.user and request.user.is_authenticated else 'Anonymous'
            logger.exception(
                f"P&L View: Unhandled exception during report generation for "
                f"{start_date} to {end_date} by user {user_pk}: {e}",
                exc_info=True # Includes stack trace
            )
            # Re-raise the original exception. The configured central DRF exception
            # handler is expected to catch this and return a formatted error response (e.g., 500).
            raise e

        # --- 4. Serialization (Executed on Cache Miss) ---
        # Convert the generated Python dictionary data into JSON format using the serializer.
        logger.debug("P&L View: Serializing generated report data.")
        try:
            serializer = ProfitLossStructuredResponseSerializer(report_data)
            response_data = serializer.data # This step performs the serialization.
        except Exception as e:
            # Log unexpected errors during the serialization process.
            logger.exception(
                f"P&L View: Serialization error for P&L report ({start_date} to {end_date}): {e}",
                exc_info=True
            )
            # Re-raise for the central exception handler -> likely results in 500 error.
            raise e

        # --- 5. Caching - Store Generated Data (Executed on Cache Miss) ---
        # Store the newly generated and serialized data in the cache.
        logger.debug(f"P&L View: Attempting to cache result for key {cache_key}")
        try:
            cache.set(cache_key, response_data, timeout=PNL_REPORT_CACHE_TIMEOUT)
            logger.info(f"P&L View: Stored report in cache. Key: {cache_key}, Timeout: {PNL_REPORT_CACHE_TIMEOUT}s")
        except Exception as e:
            # Log errors during cache storage but don't fail the request delivery.
            # The user still gets the data, just the caching failed.
            logger.error(f"P&L View: Failed to cache report for key {cache_key}: {e}", exc_info=True)

        # --- 6. Return Freshly Generated Response ---
        # Return the newly generated and serialized data.
        logger.debug("P&L View: Returning newly generated response.")
        return Response(response_data, status=status.HTTP_200_OK)