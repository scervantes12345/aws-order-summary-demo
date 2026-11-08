"""Optional end-to-end browser check for the support triage demo. Skipped unless Playwright is installed.

Run with: python3 -m pip install playwright && python3 -m playwright install chromium
(or set CHROME_PATH to an existing Chrome/Chromium binary).
"""

import importlib.util
import os
import threading
import unittest

from support_triage_demo.server import App, create_server


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is not installed (optional browser test)')
class SupportTriageBrowserTest(unittest.TestCase):
    def test_triage_review_timeout_and_evaluation(self):
        from playwright.sync_api import sync_playwright
        server = create_server(App(), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_PATH'))
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
                self.assertIn('SIMULATED', page.inner_text('#sim-banner'))

                page.click('button.ticket[data-id="T-101"]')
                page.click('#triage')
                page.wait_for_selector('#routed:not(.hidden)')
                self.assertEqual(page.inner_text('#r-queue'), 'billing')
                self.assertIn('Routed to billing (automatic)', page.inner_text('#routed'))

                page.select_option('#provider', 'bedrock-simulated')
                self.assertIn('SIMULATED', page.inner_text('#provider-note'))
                page.click('button.ticket[data-id="T-106"]')
                page.click('#triage')
                page.wait_for_selector('#review:not(.hidden)')
                self.assertIn('Amazon Bedrock (SIMULATED)', page.inner_text('#r-provider'))
                page.select_option('#review-queue', 'returns')
                page.click('#approve')
                page.wait_for_selector('#routed:not(.hidden)')
                self.assertIn('Routed to returns (human reviewer)', page.inner_text('#routed'))

                page.select_option('#fault', 'timeout')
                page.click('button.ticket[data-id="T-104"]')
                page.click('#triage')
                page.wait_for_selector('#error:not(.hidden)')
                self.assertIn('FAIL-SAFE', page.inner_text('#error'))
                self.assertIn('timeout', page.inner_text('#error'))
                self.assertEqual(page.inner_text('#r-queue'), 'human_review')
                self.assertFalse(page.is_hidden('#review'))

                page.click('#run-eval')
                page.wait_for_selector('#eval-table')
                self.assertEqual(page.locator('#eval-table tbody tr').count(), 8)
                self.assertEqual(page.locator('#eval-table .pass').count(), 16)
                self.assertIn('8/8 checks passed', page.inner_text('#eval-summary'))
                self.assertIn('not model quality, latency, or cost', page.inner_text('#eval-disclaimer'))
                self.assertEqual(errors, [])
                browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
