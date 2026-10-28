"""Known-answer check of build/webpush against the example in RFC 8291 Appendix A."""
import json
import os
import subprocess
import unittest

from macmote.push import WEBPUSH

RFC_8291_INPUT = {
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "plaintext": "When I grow up, I want to be a watermelon",
}
RFC_8291_BODY = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                 "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


@unittest.skipUnless(os.access(WEBPUSH, os.X_OK), "build/webpush not built (scripts/build.sh)")
class WebPushHelperTests(unittest.TestCase):
    def test_rfc_8291_known_answer(self):
        out = subprocess.run([WEBPUSH, "encrypt"], input=json.dumps(RFC_8291_INPUT), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout)["body"], RFC_8291_BODY)

    def test_keygen_yields_uncompressed_p256_point(self):
        out = subprocess.run([WEBPUSH, "keygen"], capture_output=True, text=True, check=True)
        keys = json.loads(out.stdout)
        self.assertTrue(keys["public_key"].startswith("B"))  # 0x04 prefix of an uncompressed point
        self.assertEqual(len(keys["private_key"]), 43)
