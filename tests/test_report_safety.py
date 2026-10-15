import unittest
from html import unescape

from app.report_safety import ReportHTMLInvalid, ascii_fragment, validate_fragment
from app.report_html import render_report
from test_report_html import report


class ReportSafetyTests(unittest.TestCase):
    def test_damaged_fragments_are_rejected(self):
        cases = ['', '<p>unfinished', '<p title="unfinished', '<p>ok</p><td style="border:1px soli',
                 '<table><tbody><tr><td>x</tr></td></tbody></table>',
                 '<p>prefix<h1>Title</h1></p>', '<div><td>orphan</td></div>',
                 '<table><div>outside row</div></table>', '<div/>',
                 '<p>ok</p></article>', '<style>body{display:none}</style>',
                 '<p>\x00</p>', '<p>\ud800</p>', '<p>\ufffd</p>']
        for html in cases:
            with self.subTest(html=repr(html)):
                with self.assertRaises(ReportHTMLInvalid):
                    validate_fragment(html)

    def test_unicode_entities_preserve_text_and_urls(self):
        html = '<p>中文 ✅ ❌ “quote” &amp; &lt;td&gt;<br><a href="https://example.com/中文?q=✓&amp;b=2">中文</a></p>'
        safe = ascii_fragment(html)
        self.assertTrue(safe.isascii())
        self.assertEqual(unescape(safe), unescape(html))
        self.assertEqual(ascii_fragment(safe), safe)
        self.assertEqual(safe.encode().decode('cp1252'), safe)

    def test_large_report_keeps_every_issue_and_closing_tag(self):
        data = report()
        data['issues_found'] = [dict(issue=f'#{i:03}', pic='Designer', severity='high',
                                    page='https://example.com/', problem='中文 <td> & "quote"',
                                    recommended_fix='Fix ✅') for i in range(1, 101)]
        html = render_report(data)
        validate_fragment(html)
        self.assertTrue(html.isascii())
        self.assertEqual(html.count('<article'), 100)
        self.assertEqual(html.count('</article>'), 100)
        self.assertIn('Issue #100', html)
        self.assertTrue(html.endswith('</article>'))
