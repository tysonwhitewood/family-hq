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


class DashboardBrowserTests(unittest.TestCase):
    def test_successful_classification_focus_survives_account_rerender(self):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as error:
            self.skipTest(f"Selenium is unavailable: {error}")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1200,900")
        options.add_argument("--no-sandbox")
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as error:
            self.skipTest(f"Headless Chrome is unavailable: {error}")
        self.addCleanup(driver.quit)
        driver.set_script_timeout(10)
        driver.get(Path("dashboard.html").resolve().as_uri())

        result = driver.execute_async_script(
            """
            const done = arguments[0];
            (async () => {
              document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
              document.getElementById('page-finance').classList.add('active');
              _finAccounts = [{
                id: 7,
                name: 'Family everyday',
                ownership: 'personal',
                account_type: 'cash',
                source_filenames: [],
                legacy: false
              }];
              const summary = [{
                name: 'Family everyday',
                account_key: 'registered:7',
                count: 2,
                balance: 100,
                last_date: '2026-08-02',
                ownership: 'personal',
                account_type: 'cash'
              }];
              const accountsBody = document.getElementById('fin-accounts-body');
              accountsBody.innerHTML = finRenderAccounts(summary);
              const oldButton = accountsBody.querySelector('[data-account-key="registered:7"]');
              oldButton.focus();
              oldButton.click();
              await new Promise(resolve => setTimeout(resolve, 0));

              authFetch = async () => ({
                ok: true,
                json: async () => ({account: {id: 7}})
              });
              loadFinance = async () => {
                _finAccounts = [{
                  id: 7,
                  name: 'Renamed everyday',
                  ownership: 'business',
                  account_type: 'cash',
                  source_filenames: [],
                  legacy: false
                }];
                accountsBody.innerHTML = finRenderAccounts([{
                  ...summary[0],
                  name: 'Renamed everyday',
                  ownership: 'business'
                }]);
              };
              loadBudget = async () => {};
              document.getElementById('fin-account-name').value = 'Renamed everyday';
              document.getElementById('fin-account-ownership').value = 'business';

              await finSaveAccountClassification();
              await new Promise(resolve => setTimeout(resolve, 0));
              const newButton = accountsBody.querySelector('[data-account-key="registered:7"]');
              const matchingFocusRestored = document.activeElement === newButton;

              _finAccounts = [{
                id: 8,
                name: 'Account being removed',
                ownership: 'personal',
                account_type: 'cash',
                source_filenames: [],
                legacy: false
              }];
              accountsBody.innerHTML = finRenderAccounts([{
                ...summary[0],
                name: 'Account being removed',
                account_key: 'registered:8'
              }]);
              const removedAccountButton = accountsBody.querySelector('[data-account-key="registered:8"]');
              removedAccountButton.focus();
              removedAccountButton.click();
              await new Promise(resolve => setTimeout(resolve, 0));
              loadFinance = async () => {
                _finAccounts = [];
                accountsBody.innerHTML = finRenderAccounts([]);
              };
              document.getElementById('fin-account-name').value = 'Account being removed';
              await finSaveAccountClassification();
              await new Promise(resolve => setTimeout(resolve, 0));
              const fallback = document.getElementById('fin-upload-open');

              done({
                oldButtonRemoved: !oldButton.isConnected,
                newButtonRendered: Boolean(newButton),
                focusRestoredToNewButton: matchingFocusRestored,
                focusAccountKey: newButton?.dataset?.accountKey || null,
                fallbackFocusedWhenAccountMissing: document.activeElement === fallback
              });
            })().catch(error => done({error: String(error), stack: error.stack}));
            """
        )

        self.assertEqual(
            result,
            {
                "oldButtonRemoved": True,
                "newButtonRendered": True,
                "focusRestoredToNewButton": True,
                "focusAccountKey": "registered:7",
                "fallbackFocusedWhenAccountMissing": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
