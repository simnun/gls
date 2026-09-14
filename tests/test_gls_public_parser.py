import unittest
from unittest.mock import patch
from types import SimpleNamespace
from core.gls import GLSClient

HTML = b'''<html><body><table>
<tr><th>Data e Ora</th><th>Luogo</th><th>Stato</th><th>Note</th></tr>
<tr><td>08/09/2026 19:54</td><td>Nola</td><td>Partita dalla sede mittente. In transito.</td><td></td></tr>
<tr><td>09/09/2026 11:32</td><td>Napoli</td><td>Spedizione in giacenza presso la sede GLS, siamo in attesa di istruzioni.</td><td>II avviso</td></tr>
</table></body></html>'''


class TestGLSPublicParser(unittest.TestCase):
    def test_public_tracking_parser(self):
        cfg = SimpleNamespace(gls_tracking_configured=True, gls_site='NI', gls_contract_code='5038', gls_customer_code='x', gls_track_endpoint='https://invalid.example', request_timeout_seconds=2)
        c = GLSClient(cfg)
        with patch.object(c, '_get_bytes', return_value=HTML):
            out = c.track_public('NI123')
        self.assertEqual(len(out['events']), 2)
        self.assertIn('giacenza', out['current_event']['state'].lower())


if __name__ == '__main__':
    unittest.main()
