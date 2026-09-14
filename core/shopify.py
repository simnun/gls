from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ShopifyError(RuntimeError):
    pass


class ShopifyClient:
    def __init__(self, config):
        self.config = config
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _request_json(self, req: Request) -> dict[str, Any]:
        try:
            with urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ShopifyError(f"Shopify HTTP {exc.code}: {body[:800]}") from exc
        except URLError as exc:
            raise ShopifyError(f"Errore rete Shopify: {exc}") from exc

    def get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not self.config.shopify_configured:
            raise ShopifyError("Shopify non configurato")

        body = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.config.shopify_client_id,
                "client_secret": self.config.shopify_client_secret,
            }
        ).encode("utf-8")
        req = Request(
            f"https://{self.config.shopify_shop}.myshopify.com/admin/oauth/access_token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        payload = self._request_json(req)
        token = payload.get("access_token")
        if not token:
            raise ShopifyError(f"Token Shopify non ricevuto: {payload}")
        self._token = token
        self._token_expires_at = time.time() + int(payload.get("expires_in", 86399))
        return token

    def graphql(self, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        token = self.get_token()
        req = Request(
            f"https://{self.config.shopify_shop}.myshopify.com/admin/api/{self.config.shopify_api_version}/graphql.json",
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            method="POST",
        )
        payload = self._request_json(req)
        return payload.get("data") or {}, payload.get("errors") or []

    def list_recent_orders(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        """Ordini da esaminare.

        `since_iso` restringe la ricerca a cio' che e' cambiato dall'ultima
        sincronizzazione: scandire ogni volta l'intera finestra costa il grosso
        del tempo disponibile. Le spedizioni ancora aperte non vanno perse,
        perche' sono gia' nel database e vengono riprese da li'.
        """
        finestra = (
            datetime.now(timezone.utc) - timedelta(days=self.config.shopify_lookback_days)
        ).date().isoformat()
        since = since_iso or finestra
        search_query = f"updated_at:>={since}"
        full_query = r"""
        query MonitorOrders($first: Int!, $after: String, $query: String!) {
          orders(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              legacyResourceId
              name
              createdAt
              updatedAt
              displayFinancialStatus
              displayFulfillmentStatus
              totalPriceSet { shopMoney { amount currencyCode } }
              paymentGatewayNames
              email
              phone
              shippingAddress { city provinceCode phone }
              customer {
                id
                legacyResourceId
                displayName
                defaultEmailAddress { emailAddress }
                defaultPhoneNumber { phoneNumber }
              }
              fulfillments {
                id
                status
                displayStatus
                createdAt
                updatedAt
                trackingInfo(first: 10) { company number url }
              }
            }
          }
        }
        """
        minimal_query = r"""
        query MonitorOrders($first: Int!, $after: String, $query: String!) {
          orders(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              legacyResourceId
              name
              createdAt
              updatedAt
              displayFinancialStatus
              displayFulfillmentStatus
              totalPriceSet { shopMoney { amount currencyCode } }
              paymentGatewayNames
              fulfillments {
                id
                status
                displayStatus
                createdAt
                updatedAt
                trackingInfo(first: 10) { company number url }
              }
            }
          }
        }
        """

        try:
            return self._paginate_orders(full_query, search_query)
        except ShopifyError as exc:
            message = str(exc).lower()
            if any(term in message for term in ["protected", "customer", "access denied", "permission"]):
                return self._paginate_orders(minimal_query, search_query)
            raise

    def _paginate_orders(self, query: str, search_query: str) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        after = None
        pages = 0
        while True:
            data, errors = self.graphql(
                query,
                {"first": 100, "after": after, "query": search_query},
            )
            if errors:
                raise ShopifyError("GraphQL Shopify: " + json.dumps(errors, ensure_ascii=False)[:2000])
            connection = data.get("orders")
            if not connection:
                raise ShopifyError("Risposta Shopify priva del campo orders")
            orders.extend(connection.get("nodes") or [])
            pages += 1
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage") or not page_info.get("endCursor") or pages >= 25:
                break
            after = page_info["endCursor"]
        return orders

    def extract_gls_shipments(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for order in orders:
            fulfillments = order.get("fulfillments") or []
            for fulfillment in fulfillments:
                for tracking in fulfillment.get("trackingInfo") or []:
                    number = str(tracking.get("number") or "").strip()
                    if not number or number in seen:
                        continue
                    company = str(tracking.get("company") or "").strip()
                    url = str(tracking.get("url") or "").strip()
                    company_l = company.lower()
                    url_l = url.lower()
                    # Store multi-corriere: accettiamo solo tracking esplicitamente GLS.
                    # Nessuna euristica sul formato del numero, per evitare falsi positivi.
                    is_gls = (
                        "gls" in company_l
                        or "general logistics systems" in company_l
                        or "gls-italy.com" in url_l
                        or "gls-group.com" in url_l
                    )
                    if not is_gls:
                        if not self.config.gls_accept_unlabeled_tracking:
                            continue
                        # Compatibilita opzionale: tracking senza corriere esplicito.
                        # Disattivata di default nella build Zuiki.
                        if len(fulfillment.get("trackingInfo") or []) > 1:
                            continue
                    seen.add(number)

                    customer = order.get("customer") or {}
                    ship_addr = order.get("shippingAddress") or {}
                    default_email = customer.get("defaultEmailAddress") or {}
                    default_phone = customer.get("defaultPhoneNumber") or {}
                    money = ((order.get("totalPriceSet") or {}).get("shopMoney") or {})
                    gateways = order.get("paymentGatewayNames") or []
                    gateway_text = " ".join(str(x).lower() for x in gateways)
                    is_cod = any(
                        key in gateway_text
                        for key in ["contrassegno", "cash on delivery", "cash_on_delivery", "cod"]
                    )
                    result.append(
                        {
                            "tracking_number": number,
                            "tracking_company": company,
                            "tracking_url_shopify": url,
                            "order_gid": order.get("id"),
                            "order_legacy_id": str(order.get("legacyResourceId") or ""),
                            "order_name": order.get("name"),
                            "order_created_at": order.get("createdAt"),
                            "fulfillment_created_at": fulfillment.get("createdAt"),
                            "fulfillment_updated_at": fulfillment.get("updatedAt"),
                            "customer_gid": customer.get("id"),
                            "customer_legacy_id": str(customer.get("legacyResourceId") or ""),
                            "customer_name": customer.get("displayName"),
                            "customer_email": default_email.get("emailAddress") or order.get("email"),
                            "customer_phone": default_phone.get("phoneNumber") or order.get("phone") or ship_addr.get("phone"),
                            "city": ship_addr.get("city"),
                            "province": ship_addr.get("provinceCode"),
                            "total_amount": float(money.get("amount")) if money.get("amount") else None,
                            "currency": money.get("currencyCode"),
                            "payment_gateways": gateways,
                            "is_cod": is_cod,
                            "shopify_financial_status": order.get("displayFinancialStatus"),
                            "shopify_fulfillment_status": order.get("displayFulfillmentStatus"),
                        }
                    )
        return result
