import unittest
from types import SimpleNamespace
from core.shopify import ShopifyClient


class TestShopifyGLSOnly(unittest.TestCase):
    def setUp(self):
        self.client = ShopifyClient(SimpleNamespace(gls_accept_unlabeled_tracking=False))

    def _order(self, company, url, number):
        return [{
            "id": "gid://shopify/Order/1",
            "legacyResourceId": "1",
            "name": "#1",
            "createdAt": "2026-09-09T10:00:00Z",
            "displayFinancialStatus": "PAID",
            "displayFulfillmentStatus": "FULFILLED",
            "totalPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "EUR"}},
            "paymentGatewayNames": ["Shopify Payments"],
            "fulfillments": [{
                "trackingInfo": [{"company": company, "url": url, "number": number}]
            }]
        }]

    def test_non_gls_is_excluded(self):
        rows = self.client.extract_gls_shipments(self._order("BRT", "https://vas.brt.it/", "123"))
        self.assertEqual(rows, [])

    def test_gls_is_included(self):
        rows = self.client.extract_gls_shipments(self._order("GLS", "https://www.gls-italy.com/", "NI123456789"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tracking_number"], "NI123456789")


if __name__ == "__main__":
    unittest.main()
