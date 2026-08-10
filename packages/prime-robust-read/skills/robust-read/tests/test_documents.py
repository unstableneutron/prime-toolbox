from __future__ import annotations

# ruff: noqa: E501 -- compact base64 encodes tiny upstream binary fixtures.
import base64
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from prime_robust_read import ReadLedger, read

# Small deterministic fixtures from firecrawl/anydoc v0.1.7's MIT-licensed
# robustness suite. See THIRD_PARTY_NOTICES.md for provenance.
FIXTURES = {
    "sample.docx": "UEsDBBQAAAAIAAAAIVyKUntm+QAAADICAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbK2Ru07DMBSGd57C8lolDgwIoaYduIzAUB7gyD5JLHyTj1uat+ekgQyowMJo/5fvl73eHr0TB8xkY2jlZd1IgUFHY0PfytfdY3UjBRUIBlwM2MoRSW43F+vdmJAEhwO1cigl3SpFekAPVMeEgZUuZg+Fj7lXCfQb9KiumuZa6RgKhlKVqUNy2T12sHdFPBz5fl6S0ZEUd7NzgrUSUnJWQ2FdHYL5hqk+ETUnTx4abKIVG6Q6j5iknwlfwWd+nGwNihfI5Qk829R7zEaZqPeeo/XvPWeWxq6zGpf81JZy1EjEr+5dvSgebFj9OYTK6JD+f8bcu/DV6cs3H1BLAwQUAAAACAAAACFcP63++q8AAAAsAQAACwAAAF9yZWxzLy5yZWxzjc87DsIwDADQnVNE3mlaBoRQQxeE1BWVA0SJm1Y0H8Xh09uTgQEqBkb/nu26edqJ3THS6J2AqiiBoVNej84IuHSn9Q4YJem0nLxDATMSNIdVfcZJpjxDwxiIZcSRgCGlsOec1IBWUuEDulzpfbQy5TAaHqS6SoN8U5ZbHj8NWKCs1QJiqytg3RzwH9z3/ajw6NXNoks/diw6siyjwSTg4aPm+p0uMgs8n8O/njy8AFBLAwQUAAAACAAAACFc7p6ECUIBAAAXBAAAEQAAAHdvcmQvZG9jdW1lbnQueG1srVPdTsMgFL73KQj3jm4XxpCVZTMx3ph4oQ9A4axtQoEAa93bC3R1JsZmTm/g8PP98JGz3rx3CvXgfGt0iZeLAiPQwshW1yV+e328vcfIB64lV0ZDiY/g8YbdrAcqjTh0oAOKDNrTocRNCJYS4kUDHfcLY0HHs71xHQ9x6WoyGCetMwK8jwKdIquiuCMdbzVmkbIy8pi5Q6XG2bE8voxzpZ6AS3CErclpO98S+VSkW2QqBmrTMAKTR+otF/EF1oEH1wNmiQxtMyQDR7g9kbC/Mu8uZR5o/wyuBhQLrkocaQJ3AZMrRB9+Fs2RzcU66RdJeaC1a+UO4vd9GlvhmeSnV/zS9UEn5StsJ3vbfTjbXs67a/4jYwU1F0eUsZd/b3NNMCcpYfSM0hQOOffMF2LGcz6hARR4peA7T0bmtkvF1NLsA1BLAwQUAAAACAAAACFcE0rkUJgAAADXAAAADwAAAHdvcmQvc3R5bGVzLnhtbF2OOw7CMBBErxK5JxsoEIri0CBqCjiAZW8+kr0beQ0ht8eRQgHlm9EbTXN+B1+8MMrIpNW+rFSBZNmN1Gv1uF93J1VIMuSMZ0KtFhR1bpu5lrR4lCLrJPWs1ZDSVAOIHTAYKXlCyl3HMZiUMfYwc3RTZIsieT14OFTVEYIZSa2Dju0FO/P0SVaMt7jhRtA28BvDnwTfV+0HUEsBAhQAFAAAAAgAAAAhXIpSe2b5AAAAMgIAABMAAAAAAAAAAAAAAIABAAAAAFtDb250ZW50X1R5cGVzXS54bWxQSwECFAAUAAAACAAAACFcP63++q8AAAAsAQAACwAAAAAAAAAAAAAAgAEqAQAAX3JlbHMvLnJlbHNQSwECFAAUAAAACAAAACFc7p6ECUIBAAAXBAAAEQAAAAAAAAAAAAAAgAECAgAAd29yZC9kb2N1bWVudC54bWxQSwECFAAUAAAACAAAACFcE0rkUJgAAADXAAAADwAAAAAAAAAAAAAAgAFzAwAAd29yZC9zdHlsZXMueG1sUEsFBgAAAAAEAAQA9gAAADgEAAAAAA==",
    "sample.pptx": "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",
    "sample.xlsx": "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",
    "sample.odt": "UEsDBBQAAAAAAAAAIVxexjIMJwAAACcAAAAIAAAAbWltZXR5cGVhcHBsaWNhdGlvbi92bmQub2FzaXMub3BlbmRvY3VtZW50LnRleHRQSwMEFAAAAAgAAAAhXE4RfIN8AQAAYAQAAAsAAABjb250ZW50LnhtbJ1Uy27CMBC89yss34NBvRSLBPXWSm3PvZqwgNX4IXudwN/XzgMCgirqJcnuzsyO13ZW66OqSA3OS6NzupjN6bp4WpndTpbAt6YMCjRmpdEY3ySCteddNafBaW6El55rocBzLLmxoAcWH6N5kn7qBRCOOJWesFdkj6dqcvMWfEWPz5weEC1nrGmaWfM8M27Pvj8/2GK5fGGtmhUl0MscRECjBMoya/V8rLS2KumxS5GuUSLn9CsoegWpoIaqA2Y6qA040hVTPk6dDvSgsp1xsVVKsqjBbvqk1GNTfWVjtqdiCJLA2EwaA5fbnFYeX2nno3d23/wBxBZc0WVs8daGpJF4MAGJIEq4H3C9U1uMLffUsZpEUGcto+EurwU9ZGFjprOGBQqHWS2qEFe4mNOLFmhSS0EctJB/uIG0ifov4iUzqNjiPd4n54LFeO2IFU7snbCH2VlmvGHtV7qBUgfIUmr65t0MDqoayCDlSVKZ7JyND9Q56s4ae/DDKH4BUEsBAhQAFAAAAAAAAAAhXF7GMgwnAAAAJwAAAAgAAAAAAAAAAAAAAIABAAAAAG1pbWV0eXBlUEsBAhQAFAAAAAgAAAAhXE4RfIN8AQAAYAQAAAsAAAAAAAAAAAAAAIABTQAAAGNvbnRlbnQueG1sUEsFBgAAAAACAAIAbwAAAPIBAAAAAA==",
    "sample.rtf": "e1xydGYxXGFuc2lcYW5zaWNwZzEyNTJcZGVmZjAKe1xmb250dGJse1xmMFxmY2hhcnNldDAgQXJpYWw7fX0Ke1xzdHlsZXNoZWV0e1xzMCBOb3JtYWw7fXtcczEgUHJlZm9ybWF0dGVkIFRleHQ7fXtcczIgUXVvdGF0aW9uczt9e1xzMyBRdW90ZTt9e1xzNFxzYmFzZWRvbjEgTGlzdGluZzt9fQpccGFyZFxwbGFpblxzMCBCb2R5IGJlZm9yZS5ccGFyClxwYXJkXHBsYWluXHMxIGZuIG1haW4oKSBce1xwYXIKXHBhcmRccGxhaW5cczQgICAgIHByaW50bG4hKFwnMjJva1wnMjIpO1xwYXIKXHBhcmRccGxhaW5cczEgXH1ccGFyClxwYXJkXHBsYWluXHMwIEJvZHkgYmV0d2Vlbi5ccGFyClxwYXJkXHBsYWluXHMyIEEgcXVvdGF0aW9uLlxwYXIKXHBhcmRccGxhaW5cczMgSXRzIHNlY29uZCBwYXJhZ3JhcGguXHBhcgpccGFyZFxwbGFpblxzMCBCb2R5IGFmdGVyLlxwYXIKfQ==",
    "resource.ods": "UEsDBBQAAAAAAAAAIVyFbDmKLgAAAC4AAAAIAAAAbWltZXR5cGVhcHBsaWNhdGlvbi92bmQub2FzaXMub3BlbmRvY3VtZW50LnNwcmVhZHNoZWV0UEsDBBQAAAAIAAAAIVyZDCyC4QAAADcCAAALAAAAY29udGVudC54bWyNUs1uwyAMvvcpIu5Z2uMQoQ+xJyDEbSOBQRi69O1HSKIl0SaVAxj7+8EW4jpaUz0h0OCwZZePM7vKk3C326CB904nCxhr7TDms8pgJD5XW5YCcqdoII7KAvGoufOAK4tv0XySPi0CUXXmbX4B7+kwxrfZGVvIv111rn/J9UI+gOrpARClmK3KXs3xJN2yr4m9KdbBfa+AZDsIU4LqAB5UhL5ln+eyjjQNxlSL8VOZBHV8+axPMQx4Z3tJ7UyyuFW9FElRWvJyFM0S5eBgkn2bw3sPqUz6awLNbkbNP/9A/gBQSwECFAAUAAAAAAAAACFchWw5ii4AAAAuAAAACAAAAAAAAAAAAAAAgAEAAAAAbWltZXR5cGVQSwECFAAUAAAACAAAACFcmQwsguEAAAA3AgAACwAAAAAAAAAAAAAAgAFUAAAAY29udGVudC54bWxQSwUGAAAAAAIAAgBvAAAAXgEAAAAA",
    "encrypted.odt": "UEsDBBQAAAAAAAAAIVxexjIMJwAAACcAAAAIAAAAbWltZXR5cGVhcHBsaWNhdGlvbi92bmQub2FzaXMub3BlbmRvY3VtZW50LnRleHRQSwMEFAAAAAgAAAAhXLrC2OAXAAAAFQAAAAsAAABjb250ZW50LnhtbGNgTM4syEgtKkmtKNEtLknMS9HNzAMAUEsDBBQAAAAIAAAAIVxXlY4QCgEAADkCAAAVAAAATUVUQS1JTkYvbWFuaWZlc3QueG1sdVHLbsIwELz3KyLfTQjqycIgAkKVuFTqF1jOprHwI7I3Kfn7Oik0jhB7Ws+MZ7ze7f5mdNaDD8pZTorVmux3b1sjrKohIHs0WZTZ8H/kpPOWORFUYFYYCAwlcy3YysnOgEW21LPROPWtlQYadX7IZqzTmrYCG06ksxjZVXQhs8BApQTFoQVOEG6Yj3TqClb6ocU4Cq0EivmmbEBeQ2ful78+DkVeXMizgJNDrIWp0N/OK2xM9gzRcXhOSu1+ahWa7HguE1NlFSqhVRDTk3qQ6HxUxyJ5GnGFgVbgVT8Jsxf4PeyzvJzOmzQGwf8JpOts3E2x3rwnfBA6gsdYU2r+6rsW3LygBfxodr9QSwECFAAUAAAAAAAAACFcXsYyDCcAAAAnAAAACAAAAAAAAAAAAAAAgAEAAAAAbWltZXR5cGVQSwECFAAUAAAACAAAACFcusLY4BcAAAAVAAAACwAAAAAAAAAAAAAAgAFNAAAAY29udGVudC54bWxQSwECFAAUAAAACAAAACFcV5WOEAoBAAA5AgAAFQAAAAAAAAAAAAAAgAGNAAAATUVUQS1JTkYvbWFuaWZlc3QueG1sUEsFBgAAAAADAAMAsgAAAMoBAAAAAA==",
}


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = ReadLedger()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_fixture(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(base64.b64decode(FIXTURES[name]))
        return path

    def write_open_document(self, extension: str, body: str) -> Path:
        media_types = {
            "ods": "application/vnd.oasis.opendocument.spreadsheet",
            "odp": "application/vnd.oasis.opendocument.presentation",
        }
        path = self.root / f"sample.{extension}"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", media_types[extension], compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "content.xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
                <office:document-content
                  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
                  xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
                  office:version="1.2"><office:body>{body}</office:body>
                </office:document-content>""",
            )
        return path

    def test_current_anydoc_converts_representative_document_families(self):
        expected = {
            "sample.docx": "word",
            "sample.pptx": "powerpoint",
            "sample.xlsx": "excel",
            "sample.odt": "opendocument-text",
            "sample.rtf": "rtf",
        }
        for name, family in expected.items():
            with self.subTest(name=name):
                result = read(self.write_fixture(name), ledger=self.ledger, use_fff=False)
                self.assertEqual(result.status, "ok", result)
                self.assertEqual(result.format, family)
                self.assertEqual(result.conversion["backend"], "firecrawl-anydoc")
                self.assertTrue(result.content.strip())

        csv = self.root / "sample.csv"
        csv.write_text('name,value\n"alpha",1\n', encoding="utf-8")
        csv_result = read(csv, ledger=self.ledger, use_fff=False)
        self.assertEqual(csv_result.format, "csv")
        self.assertIn("alpha", csv_result.content)

        epub = self.root / "sample.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
                  version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"
                  media-type="application/oebps-package+xml"/></rootfiles></container>""",
            )
            archive.writestr(
                "OEBPS/content.opf",
                """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"
                  version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                  <dc:title>Test</dc:title><dc:identifier>id</dc:identifier>
                  <dc:language>en</dc:language></metadata><manifest>
                  <item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/>
                  </manifest><spine><itemref idref="c"/></spine></package>""",
            )
            archive.writestr(
                "OEBPS/chapter.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body>
                <h1>EPUB heading</h1><p>EPUB body</p></body></html>""",
            )
        epub_result = read(epub, ledger=self.ledger, use_fff=False)
        self.assertEqual(epub_result.format, "epub")
        self.assertIn("EPUB body", epub_result.content)

        ods = self.write_open_document(
            "ods",
            """<office:spreadsheet><table:table table:name="Sheet 1">
            <table:table-row><table:table-cell office:value-type="string">
            <text:p>ODS body</text:p></table:table-cell></table:table-row>
            </table:table></office:spreadsheet>""",
        )
        ods_result = read(ods, ledger=self.ledger, use_fff=False)
        self.assertEqual(ods_result.format, "opendocument-spreadsheet")
        self.assertIn("ODS body", ods_result.content)

        odp = self.write_open_document(
            "odp",
            """<office:presentation><draw:page draw:name="Slide 1">
            <draw:frame><draw:text-box><text:p>ODP body</text:p></draw:text-box>
            </draw:frame></draw:page></office:presentation>""",
        )
        odp_result = read(odp, ledger=self.ledger, use_fff=False)
        self.assertEqual(odp_result.format, "opendocument-presentation")
        self.assertIn("ODP body", odp_result.content)

    def test_every_declared_extension_routes_to_the_correct_native_parser(self):
        cases = {
            "doc": "doc",
            "docx": "docx",
            "docm": "docx",
            "ppt": "ppt",
            "pps": "ppt",
            "pot": "ppt",
            "pptx": "pptx",
            "pptm": "pptx",
            "ppsx": "pptx",
            "ppsm": "pptx",
            "xls": "xlsx",
            "xlsx": "xlsx",
            "xlsm": "xlsx",
            "xlsb": "xlsx",
            "odt": "odt",
            "ods": "ods",
            "odp": "odp",
            "rtf": "rtf",
            "epub": "epub",
            "csv": "csv",
        }
        calls = []
        fake = types.SimpleNamespace(
            to_markdown_bytes=lambda data, format_name: calls.append(format_name) or "converted"
        )
        with mock.patch("prime_robust_read.documents.importlib.import_module", return_value=fake):
            for extension, expected_format in cases.items():
                with self.subTest(extension=extension):
                    path = self.root / f"route.{extension}"
                    path.write_bytes(b"document")
                    result = read(path, ledger=ReadLedger(), use_fff=False)
                    self.assertEqual(result.status, "ok")
                    self.assertEqual(calls[-1], expected_format)

    def test_native_resource_limit_encryption_and_malformed_categories(self):
        resource = read(self.write_fixture("resource.ods"), ledger=self.ledger, use_fff=False)
        self.assertEqual(resource.category, "resource_limited")
        self.assertEqual(resource.conversion["exception"], "ResourceLimitError")

        encrypted = read(self.write_fixture("encrypted.odt"), ledger=self.ledger, use_fff=False)
        self.assertEqual(encrypted.category, "encrypted")

        malformed = self.root / "broken.docx"
        malformed.write_bytes(b"not a document")
        result = read(malformed, ledger=self.ledger, use_fff=False)
        self.assertEqual(result.category, "malformed")

    def test_missing_native_dependency_does_not_affect_text_reading(self):
        document = self.root / "missing.docx"
        document.write_bytes(b"document")
        text = self.root / "plain.txt"
        text.write_text("still works", encoding="utf-8")
        original = __import__("importlib").import_module

        def importing(name):
            if name == "anydoc":
                raise ImportError("unavailable")
            return original(name)

        with mock.patch(
            "prime_robust_read.documents.importlib.import_module", side_effect=importing
        ):
            failed = read(document, ledger=ReadLedger(), use_fff=False)
            ordinary = read(text, ledger=ReadLedger(), use_fff=False)
        self.assertEqual(failed.category, "missing_dependency")
        self.assertEqual(ordinary.content, "still works")


if __name__ == "__main__":
    unittest.main()
