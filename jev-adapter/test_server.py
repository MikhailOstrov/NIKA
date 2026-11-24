"""Focused compatibility and failure checks for the NIKA adapter."""
import io
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import server


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


def decision(intent, entity, confidence=0.8):
    return {"answers": {
        "intent": {"type": "choice", "choice": intent, "confidence": confidence},
        "entity": {"type": "choice", "choice": entity, "confidence": confidence},
    }}


class AdapterTests(unittest.TestCase):
    def test_jev_result_maps_to_real_nika_fields(self):
        with patch.object(server, "urlopen", return_value=Response(decision("about_hunting_breeding", "elk"))):
            result = server.classify_with_jev("Когда гон у лося?", "test-secret")
        self.assertEqual(result["intents"][0]["name"], "about_hunting_breeding")
        entity = result["entities"]["rrel_entity:rrel_entity"][0]
        self.assertEqual(entity["value"], "лось")
        self.assertEqual(entity["role"], "rrel_entity")
        self.assertNotIn("start", entity)
        self.assertNotIn("end", entity)

    def test_unknown_never_becomes_a_known_animal_or_intent(self):
        with patch.object(server, "urlopen", return_value=Response(decision("unknown", "unknown"))):
            result = server.classify_with_jev("Чему равна сумма?", "test-secret")
        self.assertEqual(result["intents"], [])
        self.assertEqual(result["entities"], {})

    def test_unexpected_labels_do_not_enter_the_graph(self):
        with patch.object(server, "urlopen", return_value=Response(decision("about_hunting_breeding", "dragon"))):
            with self.assertRaises(server.ClassificationError):
                server.classify_with_jev("Когда гон?", "test-secret")

    def test_multiple_animals_are_not_reduced_to_one(self):
        with patch.object(server, "urlopen", return_value=Response(decision("about_hunting_breeding", "multiple_animals"))):
            result = server.classify_with_jev("Когда гон у лося и кабана?", "test-secret")
        self.assertEqual(result["entities"], {})

    def test_explicit_two_animals_are_guarded_even_if_jev_picks_one(self):
        with patch.object(server, "urlopen", return_value=Response(decision("about_hunting_breeding", "elk"))):
            result = server.classify_with_jev("Когда гон у лося и кабана?", "test-secret")
        self.assertEqual(result["entities"], {})
        self.assertFalse(server.has_multiple_named_animals("Расскажи о лосе и лосях"))
        self.assertFalse(server.has_multiple_named_animals("Какие птицы относятся к боровой дичи?"))

    def test_remote_error_does_not_echo_secret(self):
        err = HTTPError("https://example.test", 401, "no auth test-secret", {}, None)
        with patch.object(server, "urlopen", side_effect=err):
            with self.assertRaises(server.ClassificationError) as exc:
                server.classify_with_jev("тест", "test-secret")
        self.assertNotIn("test-secret", str(exc.exception))
        self.assertIn("401", str(exc.exception))

    def test_http_failure_returns_empty_classification_with_502(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "classify_with_jev", side_effect=server.ClassificationError("test")):
                with self.assertRaises(HTTPError) as raised:
                    urlopen(f"http://127.0.0.1:{httpd.server_port}/message?q=" + quote("тест"))
                response = json.load(raised.exception)
                self.assertEqual(raised.exception.code, 502)
                self.assertEqual(response["intents"], [])
                self.assertEqual(response["entities"], {})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
