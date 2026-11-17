"""Contabilita': il file delle spedizioni del service, confrontato con il monitor."""

import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from core import contabilita
from core.db import Database

INTESTAZIONI = ["Data", "Cliente", "Cod.Contratto", "ID BDA", "Servizio/tipologia", "Nr. Spedizione",
                "Destinatario", "Peso / Volume", "Nolo", "Diritto assicurativo", "Diritto Contrassegno", None,
                "Sprinter"]


def riga(numero, nolo=3.9, contrassegno=0.0, servizio="Partenze", extra=0.47):
    return [datetime(2026, 10, 1), "ENNEPI SPA (ZUIKI)", 5038, "-5038-", servizio, numero, "Mario Rossi",
            "Peso: 1 - Vol: 0", nolo, 0.0, contrassegno, extra, 0.0]


def file_excel(righe, titolo=True):
    import openpyxl
    libro = openpyxl.Workbook()
    foglio = libro.active
    if titolo:
        foglio.append(["Partenze -5038- ENNEPI SPA (ZUIKI)"])
    foglio.append(INTESTAZIONI)
    for r in righe:
        foglio.append(r)
    buf = io.BytesIO()
    libro.save(buf)
    return buf.getvalue()


class ContabilitaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        for numero, categoria, cod in (("NI665000001", "DELIVERED", False), ("NI665000002", "DELIVERED", True),
                                       ("NI665000003", "RETURN", True), ("NI665000004", "STORAGE", False),
                                       ("NI665000005", "IN_TRANSIT", False)):
            self.db.upsert_shipment({
                "tracking_number": numero, "order_name": "#" + numero[-1], "customer_name": "Cliente",
                "city": "Napoli", "province": "NA", "total_amount": 30.0, "currency": "EUR",
                "payment_gateways": [], "is_cod": cod, "gls_status": "Stato", "gls_note": "",
                "severity": "NORMAL", "category": categoria, "closed": categoria in {"DELIVERED", "RETURN"},
                "workflow_status": "NEW", "gls_event_at": "2026-10-05T10:00:00+02:00",
            })
        # La seconda e' stata consegnata dopo una giacenza.
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO stock_cases(tracking_number, entry_event_hash, entered_at, status,
                            outcome_category, created_at, updated_at)
                            VALUES('NI665000002','g2','2026-10-02T10:00:00+02:00','CLOSED','DELIVERED','x','x')""")

    def tearDown(self):
        self.tmp.cleanup()

    def analizza(self, righe, **kw):
        intestazioni, dati = contabilita.leggi_file("partenze.xlsx", file_excel(righe, **kw))
        return intestazioni, contabilita.analizza(intestazioni, dati, self.db.spedizioni_per_contabilita())

    def test_legge_il_file_e_riconosce_le_colonne(self):
        intestazioni, analisi = self.analizza([riga(665000001)])
        self.assertEqual(intestazioni[5], "Nr. Spedizione")
        self.assertEqual(intestazioni[11], "Colonna 12")         # la colonna senza titolo
        colonne = analisi["colonne"]
        self.assertEqual(colonne["spedizione"], 5)
        self.assertEqual(colonne["contrassegno"], 10)
        nomi_importi = [intestazioni[i] for i in colonne["importi"]]
        self.assertEqual(nomi_importi, ["Nolo", "Diritto assicurativo", "Diritto Contrassegno", "Colonna 12", "Sprinter"])
        self.assertIn("Diritto assicurativo", analisi["riepilogo"]["colonne_costo"])

    def test_esiti_e_colori(self):
        _, analisi = self.analizza([
            riga(665000001), riga(665000002, contrassegno=2.0), riga(665000003, contrassegno=2.0),
            riga(665000004), riga(665000005), riga(999999999),
        ])
        esiti = [(r["esito"], r["dettaglio"]) for r in analisi["righe"]]
        self.assertEqual(esiti[0], ("consegnata", "Consegnata"))
        self.assertEqual(esiti[1], ("giacenza", "Consegnata dopo una giacenza"))
        self.assertEqual(esiti[2][0], "resa")
        self.assertEqual(esiti[3], ("giacenza", "Ancora ferma in sede GLS"))
        self.assertEqual(esiti[4][0], "viaggio")
        self.assertEqual(esiti[5][0], "sconosciuta")
        self.assertEqual(analisi["righe"][0]["costo"], 4.37)   # nolo + colonna senza titolo

    def test_controlli_per_la_contabilita(self):
        _, analisi = self.analizza([
            riga(665000001, contrassegno=2.0),     # contrassegno addebitato su ordine pagato
            riga(665000002),                        # ordine in contrassegno senza diritto
            riga(665000005), riga(665000005),       # fatturata due volte
            riga(665000005, servizio="Giacenza"),   # un servizio diverso non e' un doppione
        ])
        controlli = [r["controlli"] for r in analisi["righe"]]
        self.assertIn("Contrassegno in fattura, ma l’ordine non è in contrassegno", controlli[0])
        self.assertIn("Ordine in contrassegno, ma il contrassegno non è in fattura", controlli[1])
        self.assertIn("Fatturata 2 volte nel file", controlli[2])
        self.assertEqual(controlli[4], [])

    def test_il_file_scaricato_ha_colonne_aggiunte_e_righe_colorate(self):
        intestazioni, analisi = self.analizza([riga(665000001), riga(665000003)])
        raw = contabilita.file_analizzato(intestazioni, analisi)
        with ZipFile(io.BytesIO(raw)) as z:
            foglio = z.read("xl/worksheets/sheet1.xml").decode()
            stili = z.read("xl/styles.xml").decode()
        self.assertIn("Esito monitor", foglio)
        self.assertIn("Resa al mittente", foglio)
        self.assertIn('rgb="FFC6EFCE"', stili)   # verde
        self.assertIn('rgb="FFFFC7CE"', stili)   # rosso
        import openpyxl
        letto = openpyxl.load_workbook(io.BytesIO(raw)).active
        self.assertEqual(letto.cell(2, 6).value, 665000001)
        self.assertEqual(letto.cell(2, 14).value, "Consegnata")
        self.assertEqual(letto.cell(2, 14).fill.fgColor.rgb, "FFC6EFCE")

    def test_csv_con_importi_in_euro(self):
        testo = ";".join(str(x) for x in INTESTAZIONI[:9]) + "\n" + \
            "01/10/2026;ENNEPI;5038;-5038-;Partenze;665000001;Rossi;Peso: 1;€ 3,90\n"
        intestazioni, dati = contabilita.leggi_file("partenze.csv", testo.encode("utf-8"))
        analisi = contabilita.analizza(intestazioni, dati, self.db.spedizioni_per_contabilita())
        self.assertEqual(analisi["righe"][0]["esito"], "consegnata")
        self.assertEqual(analisi["riepilogo"]["costo_totale"], 3.9)

    def test_file_senza_colonna_spedizione(self):
        with self.assertRaises(contabilita.FileNonLeggibile):
            contabilita.leggi_file("altro.csv", b"a;b\n1;2\n")


if __name__ == "__main__":
    unittest.main()
