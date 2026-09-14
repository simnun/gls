"""Selezione del lotto di spedizioni quando la sincronizzazione ha un tempo massimo."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.sync import select_sync_batch  # noqa: E402


def shipment(number: str, last_seen: str | None = None) -> dict:
    item = {"tracking_number": number}
    if last_seen is not None:
        item["last_seen_at"] = last_seen
    return item


class SelectSyncBatchTests(unittest.TestCase):
    def test_senza_limite_passa_tutto(self):
        targets = [shipment("A"), shipment("B")]
        batch, deferred = select_sync_batch(targets, 0)
        self.assertEqual(len(batch), 2)
        self.assertEqual(deferred, 0)

    def test_sotto_il_limite_non_rinvia_nulla(self):
        targets = [shipment("A", "2026-01-01"), shipment("B", "2026-01-02")]
        batch, deferred = select_sync_batch(targets, 10)
        self.assertEqual(len(batch), 2)
        self.assertEqual(deferred, 0)

    def test_le_nuove_hanno_la_precedenza(self):
        targets = [
            shipment("vecchia", "2026-01-05"),
            shipment("nuova"),
            shipment("ferma", "2026-01-01"),
        ]
        batch, deferred = select_sync_batch(targets, 2)
        self.assertEqual([x["tracking_number"] for x in batch], ["nuova", "ferma"])
        self.assertEqual(deferred, 1)

    def test_la_rotazione_copre_tutte_le_spedizioni(self):
        # Simula piu' esecuzioni: chi viene aggiornato passa in fondo alla coda.
        stato = {f"T{i}": f"2026-01-0{i}" for i in range(1, 7)}
        visti: set[str] = set()
        for giro in range(3):
            targets = [shipment(n, stato[n]) for n in stato]
            batch, _ = select_sync_batch(targets, 2)
            for item in batch:
                numero = item["tracking_number"]
                visti.add(numero)
                stato[numero] = f"2026-02-0{giro + 1}"  # aggiornata adesso
        self.assertEqual(visti, set(stato))

    def test_non_modifica_la_lista_ricevuta(self):
        targets = [shipment("A", "2026-01-05"), shipment("B", "2026-01-01")]
        originale = list(targets)
        select_sync_batch(targets, 1)
        self.assertEqual(targets, originale)


if __name__ == "__main__":
    unittest.main()
