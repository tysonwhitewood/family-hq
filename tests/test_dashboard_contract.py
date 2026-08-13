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

    def test_cash_flow_renders_calendar_months_not_cycles(self):
        html = Path("dashboard.html").read_text()

        self.assertIn("section.months", html)
        self.assertIn("bdgtMonthEvents(month)", html)
        self.assertIn("Six calendar months", html)
        self.assertNotIn("section.cycles", html)
        self.assertNotIn("Four-week planning cycles", html)

    def test_budget_entry_uses_a_form_rather_than_chained_prompts(self):
        html = Path("dashboard.html").read_text()

        for marker in [
            'id="bdgt-entry-modal"',
            'id="bdgt-entry-direction"',
            'id="bdgt-entry-recurrence"',
            "function bdgtSaveEntry(",
            'id="bdgt-months-modal"',
            "function bdgtOpenMonths(",
            "function bdgtSaveMonths(",
            "/months",
        ]:
            self.assertIn(marker, html)

        self.assertNotIn("prompt('Money coming in or going out?", html)
        self.assertNotIn("prompt('Due date (YYYY-MM-DD):'", html)

    def test_budget_targets_use_a_form_with_frequency_and_direction(self):
        html = Path("dashboard.html").read_text()

        for marker in [
            'id="bdgt-target-modal"',
            'id="bdgt-target-frequency"',
            'id="bdgt-target-direction"',
            '<option value="biannual">',
            "function bdgtSaveTarget(",
        ]:
            self.assertIn(marker, html)

        self.assertNotIn("prompt('Category name", html)
        self.assertNotIn("prompt('Monthly budget", html)

    def test_finance_page_offers_a_categorise_window(self):
        html = Path("dashboard.html").read_text()

        for marker in [
            'id="fin-categorise-card"',
            'id="fin-categorise-body"',
            "function finLoadCategorise(",
            "function finSaveMerchantRule(",
            "function finDeleteMerchantRule(",
            "/api/finance/uncategorised",
            "/api/finance/merchant-rules",
            "optionsFor(m.ownership)",
        ]:
            self.assertIn(marker, html)

    def test_budget_page_opens_on_the_cash_flow_not_the_target_table(self):
        html = Path("dashboard.html").read_text()

        personal_card = html[html.index("Personal Budget vs Actuals") - 200:
                             html.index("Personal Budget vs Actuals")]
        self.assertNotIn("<details class=\"card\" open>", personal_card)
        self.assertIn("function bdgtFillCategoryList(", html)
        self.assertIn("No income budgeted yet", html)

    def test_budget_headline_shows_the_four_monthly_figures(self):
        html = Path("dashboard.html").read_text()

        for marker in [
            'id="bdgt-hl-business-income"',
            'id="bdgt-hl-business-expenses"',
            'id="bdgt-hl-personal-income"',
            'id="bdgt-hl-personal-expenses"',
            "function bdgtRenderHeadline(",
        ]:
            self.assertIn(marker, html)

        self.assertNotIn('id="bdgt-gauge"', html)
        self.assertNotIn("50 / 30 / 20 RULE", html)

    def test_cash_flow_leads_with_months_as_columns(self):
        html = Path("dashboard.html").read_text()

        for marker in [
            "function bdgtMonthColumns(",
            'class="bdgt-month-table"',
            "balance_as_of",
        ]:
            self.assertIn(marker, html)

    def test_savings_goals_are_gone_and_upcoming_totals_split_by_direction(self):
        html = Path("dashboard.html").read_text()

        self.assertNotIn("bdgt-goals-container", html)
        self.assertNotIn("bdgtRenderGoals", html)
        self.assertNotIn("Savings Goals", html)
        self.assertNotIn("Total Expected", html)
        self.assertIn("<span>Money out</span>", html)
        self.assertIn("<span>Money in</span>", html)

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


class DashboardReassignmentPromptTests(unittest.TestCase):
    """A refused statement re-upload must ask before moving it between accounts."""

    def _run(self, confirm_answer):
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

        return driver.execute_async_script(
            """
            const confirmAnswer = arguments[0];
            const done = arguments[arguments.length - 1];
            (async () => {
              document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
              document.getElementById('page-finance').classList.add('active');

              const sent = [];
              class FakeXhr {
                constructor() {
                  this.upload = {};
                  this.status = 0;
                  this.responseText = '';
                }
                open() {}
                abort() {}
                send(formData) {
                  sent.push(formData);
                  if (sent.length === 1) {
                    this.status = 409;
                    this.responseText = JSON.stringify({
                      error: 'statement.csv already belongs to Everyday account.',
                      conflict: 'account_reassignment',
                      current_account: 'Everyday account'
                    });
                  } else {
                    this.status = 200;
                    this.responseText = JSON.stringify({
                      parsed_count: 1,
                      latest_date: '2026-05-30'
                    });
                  }
                  setTimeout(() => this.onload(), 0);
                }
              }
              window.XMLHttpRequest = FakeXhr;

              let confirmMessage = null;
              window.confirm = message => { confirmMessage = message; return confirmAnswer; };
              loadFinance = async () => {};

              _finUploadFile = new File(['30/05/2026,-10.00,Groceries,95.00\\n'], 'statement.csv');
              document.getElementById('fin-upload-account').value = 'new';
              document.getElementById('fin-upload-account-name').value = 'Eden Commercial operating';
              document.getElementById('fin-upload-ownership').value = 'business';
              document.getElementById('fin-upload-type').value = 'cash';

              finSubmitUpload();
              await new Promise(resolve => setTimeout(resolve, 50));

              done({
                requestCount: sent.length,
                confirmShown: confirmMessage !== null,
                confirmMentionsAccount: String(confirmMessage || '').includes('Everyday account'),
                retryConfirmed: sent.length > 1
                  ? sent[1].get('confirm_reassign')
                  : null,
                firstRequestUnconfirmed: sent[0].get('confirm_reassign')
              });
            })().catch(error => done({error: String(error), stack: error.stack}));
            """,
            confirm_answer,
        )

    def test_declining_the_prompt_leaves_the_statement_on_its_account(self):
        result = self._run(False)

        self.assertEqual(result["requestCount"], 1)
        self.assertTrue(result["confirmShown"])
        self.assertTrue(result["confirmMentionsAccount"])
        self.assertIsNone(result["firstRequestUnconfirmed"])

    def test_accepting_the_prompt_retries_the_upload_with_confirmation(self):
        result = self._run(True)

        self.assertEqual(result["requestCount"], 2)
        self.assertEqual(result["retryConfirmed"], "true")


class DashboardNewAccountTests(unittest.TestCase):
    """Registered accounts must be creatable without re-uploading a statement."""

    def _driver(self):
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
        return driver

    def test_new_account_opens_an_enabled_form_with_no_registered_accounts(self):
        driver = self._driver()

        result = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
              document.getElementById('page-finance').classList.add('active');
              _finAccounts = [];

              document.getElementById('fin-account-new').click();
              await new Promise(resolve => setTimeout(resolve, 0));

              done({
                modalOpen: document.getElementById('fin-account-modal').style.display,
                editFieldsShown: document.getElementById('fin-account-edit-fields').style.display,
                linkFieldsHidden: document.getElementById('fin-account-link-fields').style.display,
                saveDisabled: document.getElementById('fin-account-save').disabled,
                saveLabel: document.getElementById('fin-account-save').textContent,
                nameEmpty: document.getElementById('fin-account-name').value === ''
              });
            })().catch(error => done({error: String(error), stack: error.stack}));
            """
        )

        self.assertEqual(result.get("modalOpen"), "flex")
        self.assertEqual(result.get("editFieldsShown"), "grid")
        self.assertEqual(result.get("linkFieldsHidden"), "none")
        self.assertFalse(result.get("saveDisabled"))
        self.assertEqual(result.get("saveLabel"), "Create account")
        self.assertTrue(result.get("nameEmpty"))

    def test_creating_an_account_posts_it_to_the_accounts_endpoint(self):
        driver = self._driver()

        result = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
              document.getElementById('page-finance').classList.add('active');
              _finAccounts = [];

              const calls = [];
              authFetch = async (url, options) => {
                calls.push({url, method: options.method, body: JSON.parse(options.body)});
                return {ok: true, json: async () => ({account: {id: 4}})};
              };
              loadFinance = async () => {};
              loadBudget = async () => {};

              document.getElementById('fin-account-new').click();
              await new Promise(resolve => setTimeout(resolve, 0));
              document.getElementById('fin-account-name').value = 'Mortgage — Great Southern';
              document.getElementById('fin-account-ownership').value = 'personal';
              document.getElementById('fin-account-type').value = 'loan';

              await finSaveAccountClassification();
              await new Promise(resolve => setTimeout(resolve, 0));

              done({calls});
            })().catch(error => done({error: String(error), stack: error.stack}));
            """
        )

        self.assertIsNone(result.get("error"))
        self.assertEqual(len(result["calls"]), 1)
        call = result["calls"][0]
        self.assertEqual(call["url"], "/api/finance/accounts")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["body"],
            {
                "name": "Mortgage — Great Southern",
                "ownership": "personal",
                "account_type": "loan",
            },
        )


if __name__ == "__main__":
    unittest.main()
