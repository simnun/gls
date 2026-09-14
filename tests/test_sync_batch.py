"""Selezione del lotto di spedizioni quando la sincronizzazione ha un tempo massimo."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.sync import select_sync_batch  # noqa: E402


def shipment(number: str, controllata: str | None = None) -> dict:
    """Spedizione con, eventualmente, l'istante dell'ultima interrogazione GLS."""
    item = {"tracking_number": number}
    if controllata is not None:
        item["gls_checked_at"] = controllata
    return item


class SelectSyncBatchTests(unittest.TestCase):
    def test_senza_limite_passa_tutto(self):
        targets = [shipment("A"), shipment("B")]
        lotto, rimandate = select_sync_batch(targets, 0)
        self.assertEqual(len(lotto), 2)
        self.assertEqual(rimandate, [])

    def test_sotto_il_limite_non_rinvia_nulla(self):
        targets = [shipment("A", "2026-01-01"), shipment("B", "2026-01-02")]
        lotto, rimandate = select_sync_batch(targets, 10)
        self.assertEqual(len(lotto), 2)
        self.assertEqual(rimandate, [])

    def test_le_mai_interrogate_hanno_la_precedenza(self):
        targets = [
            shipment("gia-vista", "2026-01-05"),
            shipment("mai-interrogata"),
            shipment("ferma", "2026-01-01"),
        ]
        lotto, rimandate = select_sync_batch(targets, 2)
        self.assertEqual([x["tracking_number"] for x in lotto], ["mai-interrogata", "ferma"])
        self.assertEqual([x["tracking_number"] for x in rimandate], ["gia-vista"])

    def test_le_rinviate_vengono_restituite_per_intero(self):
        # Servono all'chiamante per registrarle: se andassero perse, la ricerca
        # incrementale su Shopify non le ritroverebbe piu'.
        targets = [shipment(f"T{i}", f"2026-01-{i:02d}") for i in range(1, 11)]
        lotto, rimandate = select_sync_batch(targets, 3)
        self.assertEqual(len(lotto), 3)
        self.assertEqual(len(rimandate), 7)
        self.assertEqual(
            {x["tracking_number"] for x in lotto} | {x["tracking_number"] for x in rimandate},
            {x["tracking_number"] for x in targets},
        )

    def test_la_rotazione_copre_tutte_le_spedizioni(self):
        # Chi viene interrogato passa in fondo alla coda.
        stato = {f"T{i}": f"2026-01-0{i}" for i in range(1, 7)}
        visti: set[str] = set()
        for giro in range(3):
            targets = [shipment(n, stato[n]) for n in stato]
            lotto, _ = select_sync_batch(targets, 2)
            for item in lotto:
                numero = item["tracking_number"]
                visti.add(numero)
                stato[numero] = f"2026-02-0{giro + 1}"
        self.assertEqual(visti, set(stato))

    def test_non_modifica_la_lista_ricevuta(self):
        targets = [shipment("A", "2026-01-05"), shipment("B", "2026-01-01")]
        originale = list(targets)
        select_sync_batch(targets, 1)
        self.assertEqual(targets, originale)


if __name__ == "__main__":
    unittest.main()
