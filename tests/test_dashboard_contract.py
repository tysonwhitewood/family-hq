import unittest
from pathlib import Path


class DashboardContractTests(unittest.TestCase):
    def test_dashboard_contains_six_month_cash_flow_sections(self):
        html = Path("dashboard.html").read_text()

        self.assertIn('id="bdgt-personal-cashflow"', html)
        self.assertIn('id="bdgt-business-cashflow"', html)
        self.assertIn('id="bdgt-combined-cashflow"', html)
        self.assertIn("renderCashFlow", html)
        self.assertNotIn(
            "Based on last 3 months · Known upcoming expenses factored in",
            html,
        )

    def test_dashboard_exposes_forecast_controls(self):
        html = Path("dashboard.html").read_text()

        self.assertIn("bdgtEditSafetyBuffer", html)
        self.assertIn("ownership:", html)
        self.assertIn("direction:", html)
        self.assertIn("recurrence:", html)

    def test_dashboard_exposes_finance_upload_and_classification_controls(self):
        dashboard = Path("dashboard.html").read_text()

        for marker in [
            'id="fin-upload-modal"',
            'id="fin-upload-progress"',
            'id="fin-upload-status"',
            'function finSubmitUpload(',
            'xhr.upload.onprogress',
            'Processing transactions',
            'Upload successful',
            'Edit classification',
        ]:
            self.assertIn(marker, dashboard)

    def test_finance_legacy_cards_use_the_parser_account_key(self):
        dashboard = Path("dashboard.html").read_text()

        self.assertIn("account.source_filenames?.[0]", dashboard)
        self.assertIn("'legacy:' + sourceFilename.toLowerCase()", dashboard)
        self.assertNotIn(
            "'legacy:' + String(account.name || '').trim().toLowerCase()",
            dashboard,
        )

    def test_finance_retrieved_text_is_escaped_before_inner_html(self):
        dashboard = Path("dashboard.html").read_text()

        for marker in [
            "_finEsc(cat)",
            "_finEsc(w.title)",
            "_finEsc(t.description || '—')",
            "_finEsc(t.date)",
            "_finEsc(t.account)",
            "_finEsc(t.category || '')",
            "_finEsc(t.item)",
            "_finEsc(t.action)",
            "_finEsc(text)",
        ]:
            self.assertIn(marker, dashboard)

    def test_finance_modal_requests_ignore_stale_callbacks_and_can_abort(self):
        dashboard = Path("dashboard.html").read_text()

        for marker in [
            "let _finUploadXhr = null",
            "let _finUploadRequestToken = 0",
            "_finUploadXhr.abort()",
            "xhr.timeout = 120000",
            "xhr.ontimeout",
            "xhr.onabort",
            "new AbortController()",
            "let _finClassificationRequestToken = 0",
            "signal: controller.signal",
        ]:
            self.assertIn(marker, dashboard)

    def test_finance_modals_trap_focus_and_inert_the_background(self):
        dashboard = Path("dashboard.html").read_text()

        for marker in [
            "function finHandleModalKeydown(",
            "event.key === 'Tab'",
            "event.key === 'Escape'",
            "element.inert = true",
            "finRestoreModalBackground()",
            "opener.focus()",
        ]:
            self.assertIn(marker, dashboard)


if __name__ == "__main__":
    unittest.main()
