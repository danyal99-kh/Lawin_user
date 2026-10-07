"""میان‌افزارهای امنیتی.

`SecurityTicketRefreshMiddleware`: نشست امنیتی «لغزان» — هر پاسخ موفق از
endpointهای مالی، یک بلیت امنیتی تازه در هدر `X-Security-Ticket` برمی‌گرداند تا
کلاینت تا وقتی فعال است منقضی نشود و فقط نشستِ بیکارِ طولانی باطل شود.
"""

from core import security

_FINANCIAL_PREFIXES = ("/api/v1/accounting/", "/api/v1/reports/")


class SecurityTicketRefreshMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code < 400
            and request.path.startswith(_FINANCIAL_PREFIXES)
        ):
            ticket = request.headers.get(security.TICKET_HEADER) or ""
            if ticket:
                try:
                    response[security.TICKET_HEADER] = security.refresh_ticket(ticket)
                except Exception:
                    # بلیت نامعتبر/منقضی: خودِ endpoint مالی ردش می‌کند.
                    pass
        return response