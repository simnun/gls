import unittest
from types import SimpleNamespace

from core.shopify import ShopifyClient


class TestShopifyExtract(unittest.TestCase):
    def test_extract_gls_tracking(self):
        cfg = SimpleNamespace(gls_accept_unlabeled_tracking=True)
        client = ShopifyClient(cfg)
        orders = [{
            "id": "gid://shopify/Order/1",
            "legacyResourceId": "1",
            "name": "#1001",
            "createdAt": "2026-09-09T09:00:00Z",
            "displayFinancialStatus": "PENDING",
            "displayFulfillmentStatus": "FULFILLED",
            "totalPriceSet": {"shopMoney": {"amount": "55.00", "currencyCode": "EUR"}},
            "paymentGatewayNames": ["Contrassegno"],
            "customer": {"displayName": "Test", "defaultEmailAddress": {"emailAddress": "a@b.it"}, "defaultPhoneNumber": {"phoneNumber": "+39123"}},
            "shippingAddress": {"city": "Nola", "provinceCode": "NA"},
            "fulfillments": [{"trackingInfo": [{"company": "GLS", "number": "XYZ", "url": "https://gls.example/XYZ"}]}]
        }]
        result = client.extract_gls_shipments(orders)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tracking_number"], "XYZ")
        self.assertTrue(result[0]["is_cod"])


if __name__ == "__main__":
    unittest.main()
