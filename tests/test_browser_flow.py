"""Optional end-to-end browser check. Skipped unless Playwright is installed.

Run with: python3 -m pip install playwright && python3 -m playwright install chromium
(or set CHROME_PATH to an existing Chrome/Chromium binary).
"""

import importlib.util
import os
import threading
import unittest

from sales_investigator.server import create_server


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is not installed (optional browser test)')
class BrowserFlowTest(unittest.TestCase):
    def test_investigate_flow(self):
        from playwright.sync_api import sync_playwright
        server = create_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_PATH'))
                page = browser.new_page()
                page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
                self.assertEqual(page.input_value('#question'), 'Why did West sales drop yesterday?')
                page.wait_for_function("document.getElementById('mode-tag').textContent === 'Simulated Bedrock'")
                page.click('#go')
                page.wait_for_selector('details.step >> nth=3')
                self.assertIn('$800.00', page.inner_text('#summary'))
                self.assertIn('23.8%', page.inner_text('#observed'))
                self.assertIn('no inventory data', page.inner_text('#unknown'))
                self.assertEqual(page.locator('details.step code.tool').all_inner_texts(),
                                 ['list_datasets', 'compare_region_revenue', 'attribute_change_by_product',
                                  'submit_findings'])
                page.click('details.step >> nth=2 >> summary')
                self.assertIn('"share_of_region_change_pct": "80.0"', page.locator('details.step >> nth=2').inner_text())
                self.assertTrue(page.is_visible('#mode-tag'))
                browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
