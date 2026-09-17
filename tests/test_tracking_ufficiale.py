import unittest

from core.gls import GLSClient, normalize_event_at, numero_senza_sede

# Estratto reale del tracking XML di GLS: anno a due cifre, minuti oltre il
# sessantesimo (06:63 = 07:03) e campi di testata fermi alla scansione vecchia.
XML = """<?xml version="1.0" encoding="ISO-8859-1" ?><ELENCO><SPEDIZIONE>
<NumSped><![CDATA[665031172]]></NumSped>
<SedeDestinazione><![CDATA[Molassana New]]></SedeDestinazione>
<StatoSpedizione><![CDATA[Non consegnato]]></StatoSpedizione>
<Note><![CDATA[IN ATTESA DI ISTRUZIONI AL CITOFONO 12:21]]></Note>
<TRACKING>
<Data><![CDATA[17/09/26]]></Data><Ora><![CDATA[06:63]]></Ora>
<Luogo><![CDATA[Molassana New]]></Luogo>
<Stato><![CDATA[Consegna prevista nel corso della giornata odierna.]]></Stato>
<Note><![CDATA[]]></Note><Codice><![CDATA[905]]></Codice>
<Data><![CDATA[16/09/26]]></Data><Ora><![CDATA[12:21]]></Ora>
<Luogo><![CDATA[Molassana New]]></Luogo>
<Stato><![CDATA[Spedizione in giacenza presso la sede GLS, siamo in attesa di istruzioni.]]></Stato>
<Note><![CDATA[Al citofono]]></Note><Codice><![CDATA[66]]></Codice>
</TRACKING></SPEDIZIONE></ELENCO>"""


class NumeroSpedizioneTests(unittest.TestCase):
    def test_toglie_la_sigla_della_sede(self):
        self.assertEqual(numero_senza_sede("NI665031172", "NI"), "665031172")

    def test_lascia_intatto_un_numero_gia_pulito(self):
        self.assertEqual(numero_senza_sede("665031172", "NI"), "665031172")


class DataGLSTests(unittest.TestCase):
    def test_anno_a_due_cifre(self):
        self.assertEqual(normalize_event_at("16/09/26", "12:21"), "2026-09-16T12:21:00+02:00")

    def test_minuti_oltre_il_sessantesimo(self):
        self.assertEqual(normalize_event_at("17/09/26", "06:63"), "2026-09-17T07:03:00+02:00")

    def test_formato_gia_valido_resta_invariato(self):
        self.assertEqual(normalize_event_at("15/09/2026", "09:06"), "2026-09-15T09:06:00+02:00")


class TrackingUfficialeTests(unittest.TestCase):
    def setUp(self):
        self.parsed = GLSClient.parse_tracking_xml(XML, "NI665031172")

    def test_gli_eventi_sono_in_ordine_cronologico(self):
        date = [e["event_at"] for e in self.parsed["events"]]
        self.assertEqual(date, sorted(date))

    def test_l_evento_corrente_e_l_ultimo_arrivato(self):
        corrente = self.parsed["current_event"]
        self.assertEqual(corrente["event_at"], "2026-09-17T07:03:00+02:00")
        self.assertIn("Consegna prevista", corrente["state"])

    def test_la_testata_non_sovrascrive_l_ultima_scansione(self):
        corrente = self.parsed["current_event"]
        # "Non consegnato" e la nota della giacenza di ieri riporterebbero la
        # spedizione in giacenza anche dopo che e' tornata in consegna.
        self.assertNotEqual(corrente["state"], "Non consegnato")
        self.assertNotIn("ATTESA DI ISTRUZIONI", corrente["note"])


class NotaDiTestataTests(unittest.TestCase):
    """La nota di testata non deve riportare in giacenza una spedizione
    che GLS ha gia' rimesso in consegna."""

    def test_l_ultimo_evento_senza_nota_resta_senza_nota(self):
        corrente = GLSClient.parse_tracking_xml(XML, "NI665031172")["current_event"]
        self.assertEqual((corrente.get("note") or "").strip(), "")

    def test_classificazione_dell_ultimo_evento(self):
        from pathlib import Path

        from core.classifier import Classifier

        class DbFinto:
            def get_override(self, code):
                return None

        corrente = GLSClient.parse_tracking_xml(XML, "NI665031172")["current_event"]
        esito = Classifier(Path("config/rules.json"), DbFinto()).classify(
            code=corrente.get("code"), state=corrente.get("state"),
            note=corrente.get("note"), event_at=corrente.get("event_at"), is_cod=False)
        self.assertEqual(esito.category, "OUT_FOR_DELIVERY")
