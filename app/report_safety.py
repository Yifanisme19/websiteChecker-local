"""Fail closed for damaged report fragments; never repair or truncate a payload."""
from html.parser import HTMLParser


# Our conservative delivery budget, leaving room for the recipient's wrapper.
# This is not a claim about the remote database schema or API contract.
DISCUSSION_MAX_BYTES = 60_000
TAGS = {'div', 'h1', 'h2', 'p', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'span', 'a', 'article', 'br'}
PARENTS = {'thead': {'table'}, 'tbody': {'table'}, 'tr': {'thead', 'tbody', 'table'},
           'th': {'tr'}, 'td': {'tr'}}
INLINE = {'span', 'a', 'br'}


class ReportHTMLInvalid(ValueError):
    pass


class FragmentValidator(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []
        self.tags = 0

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            raise ReportHTMLInvalid('unsupported_tag')
        parent = self.stack[-1] if self.stack else None
        if tag in PARENTS and parent not in PARENTS[tag]:
            raise ReportHTMLInvalid('invalid_table_nesting')
        if parent in {'table', 'thead', 'tbody', 'tr'} and (
                tag not in PARENTS or parent not in PARENTS[tag]):
            raise ReportHTMLInvalid('invalid_table_child')
        if any(x in {'p', 'span', 'a'} for x in self.stack) and tag not in INLINE:
            raise ReportHTMLInvalid('invalid_inline_nesting')
        self.tags += 1
        if tag != 'br':
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            raise ReportHTMLInvalid('non_void_self_closing_tag')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ReportHTMLInvalid('unbalanced_tags')

    def handle_data(self, data):
        # HTMLParser treats an unfinished tag/attribute as text at EOF.
        if '<' in data or (data.strip() and (not self.stack or self.stack[-1] in
                                           {'table', 'thead', 'tbody', 'tr'})):
            raise ReportHTMLInvalid('incomplete_markup')

    def handle_comment(self, data):
        raise ReportHTMLInvalid('unexpected_comment')

    def handle_decl(self, decl):
        raise ReportHTMLInvalid('unexpected_document')

    def handle_pi(self, data):
        raise ReportHTMLInvalid('unexpected_instruction')


def validate_fragment(html):
    if not isinstance(html, str) or not html.strip():
        raise ReportHTMLInvalid('empty_html')
    try:
        html.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise ReportHTMLInvalid('invalid_unicode') from exc
    if any(ord(c) < 32 and c not in '\r\n\t' for c in html) or '\ufffd' in html:
        raise ReportHTMLInvalid('invalid_character')
    parser = FragmentValidator()
    parser.feed(html)
    # Some Python versions discard an unfinished quoted tag in close().
    if parser.rawdata:
        raise ReportHTMLInvalid('incomplete_markup')
    parser.close()
    if parser.stack or parser.rawdata or not parser.tags:
        raise ReportHTMLInvalid('unclosed_tags')


def ascii_fragment(html):
    validate_fragment(html)
    # Numeric entities survive legacy charset conversions without losing Unicode.
    return html.encode('ascii', errors='xmlcharrefreplace').decode('ascii')
