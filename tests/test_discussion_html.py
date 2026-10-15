import unittest
from html import unescape
from app.discussion_html import discussion_fragment
from app.report_html import render_report
from app.report_safety import ReportHTMLInvalid
from test_report_html import report


class FragmentTests(unittest.TestCase):
    def test_generation_does_not_silently_discard_truncated_tail(self):
        for html in ['<p>ok</p><td style="border:1px soli', '<article><p>unfinished']:
            with self.subTest(html=html), self.assertRaises(ReportHTMLInvalid):
                discussion_fragment(html)

    def test_real_report_has_inline_styles_without_document_or_source_newlines(self):
        data = report()
        data['basic_info']['phone'].append({'label': '中文', 'number': '456'})
        data['issues_found'] = [{'issue': '#001', 'pic': 'Designer', 'severity': 'high',
                                'page': 'https://example.com/?a=1&b=2',
                                'problem': '<script>bad</script>\n中文',
                                'recommended_fix': 'Fix & verify'}]
        html = render_report(data)
        for unwanted in ['<!doctype', '<html', '<head', '<meta', '<title', '<style', '<body', '<main', '\n', '\r']:
            self.assertNotIn(unwanted, html.lower())
        self.assertIn('color:#000', html)
        self.assertIn('border-collapse:collapse', html)
        self.assertIn('width:110px', html)
        self.assertTrue(html.isascii())
        self.assertIn('中文', unescape(html))
        self.assertIn('&lt;script&gt;bad&lt;/script&gt; &#20013;&#25991;', html)
        self.assertNotIn('class=', html)
        self.assertNotIn('id=', html)
        self.assertNotIn('min-width:620px', html)
        self.assertIn('https://example.com/?a=1&amp;b=2', html)
        self.assertEqual(html.count('<br'), 1)
        self.assertIn('Fix &amp; verify', html)

    def test_failed_notification_fragment_keeps_retry_link(self):
        html = discussion_fragment('<h2>Failed</h2><p><a href="https://example.com/?a=1&amp;b=2">Retry</a></p>')
        self.assertIn('Retry</a>', html)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', html)
        self.assertIn('style=', html)

    def test_head_is_omitted_and_attribute_newlines_do_not_become_br(self):
        html = discussion_fragment('<html><head><style>p{color:red}</style></head><body><main><p title="a\nb">A\nB<br>C</p></main></body></html>')
        self.assertNotIn('p{color', html)
        self.assertNotIn('\n', html)
        self.assertIn('title="a&#10;b"', html)
        self.assertIn('A B<br', html)
