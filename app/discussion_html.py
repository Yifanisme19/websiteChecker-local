"""Stored report and NEWPAGES discussion fragment; deliberately excludes document and main wrappers.

These inline styles are the report style source. No document wrappers, media
queries or pseudo-class interaction styles are emitted.
"""
from html import escape
from html.parser import HTMLParser
import re

from app.report_safety import ReportHTMLInvalid, ascii_fragment


TAG_STYLES = {
    'h1': 'font-size:24px;font-weight:600;margin:0 0 4px',
    'h2': 'font-size:18px;font-weight:600;margin:30px 0 12px',
    'table': 'width:100%;border-collapse:collapse;text-align:left;overflow-wrap:anywhere',
    'th': 'border:1px solid #ddd;padding:10px 12px;vertical-align:top;background:#f6f6f6;font-weight:600',
    'td': 'border:1px solid #ddd;padding:10px 12px;vertical-align:top',
    'a': 'color:#245b91;text-decoration:underline;text-underline-offset:2px',
}
CLASS_STYLES = {
    'draft': 'font-size:12px;color:#777;margin:0 0 24px',
    'wrap': 'overflow-x:auto;margin:0 0 20px',
    'status': 'white-space:nowrap',
    'pass': 'color:#217346',
    'fail': 'color:#b42318',
    'badge': 'display:inline-block;border:1px solid #ccc;border-radius:4px;padding:2px 8px;font-size:12px;font-weight:400;background:#fff;color:#000',
    'issue-title': 'border-bottom:1px solid #bbb;padding-bottom:10px',
}
VOID_TAGS = {'br', 'hr', 'img', 'input', 'meta', 'link', 'wbr', 'area', 'base', 'col', 'embed', 'param', 'source', 'track'}


class FragmentParser(HTMLParser):
    def __init__(self, has_main):
        super().__init__(convert_charrefs=True)
        self.has_main = has_main
        self.inside = not has_main
        self.skip = 0
        self.stack = []
        self.output = []

    def handle_starttag(self, tag, attrs):
        if tag == 'main':
            self.inside = True
            return
        if tag in {'head', 'style', 'script', 'title'}:
            self.skip += 1
            return
        if not self.inside or self.skip or tag in {'html', 'body', 'meta', 'link'}:
            return
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        parent = self.stack[-1] if self.stack else None
        index = 1
        if parent:
            parent['children'] += 1
            index = parent['children']
        styles = []
        if not parent:
            styles.append('color:#202124;font:14px/1.5 Arial,sans-serif')
        styles.append(TAG_STYLES.get(tag, ''))
        styles.extend(CLASS_STYLES[c] for c in classes if c in CLASS_STYLES)
        table = next((x for x in reversed(self.stack) if x['tag'] == 'table'), None)
        if table:
            kind = table['classes']
            if 'info' in kind:
                if tag == 'th': styles.append('width:195px')
                if tag == 'td': styles.append('overflow-wrap:anywhere')
            if 'check' in kind:
                if tag == 'td' and index == 1: styles.append('width:44px')
                if tag == 'th' and index == 3: styles.append('width:110px')
                if tag == 'th' and index == 4: styles.append('width:90px')
            if 'issue' in kind:
                if tag == 'th' and index == 1: styles.append('width:165px')
                if tag == 'th' and index == 3: styles.append('width:100px')
                if tag == 'td': styles.append('overflow-wrap:anywhere')
                if 'severity' in classes: styles.append('text-align:left;color:#b42318')
        if attrs.get('style'):
            styles.append(attrs['style'])
        # Generic class names and repeated IDs can inherit the host page's CSS.
        attrs.pop('class', None)
        attrs.pop('id', None)
        style = ';'.join(s for s in styles if s)
        if style:
            attrs['style'] = style
        rendered = ''.join(' ' + k + ('="' + escape(v, quote=True) + '"' if v is not None else '')
                           for k, v in attrs.items())
        self.output.append('<' + tag + rendered + '>')
        if tag not in VOID_TAGS:
            self.stack.append({'tag': tag, 'classes': classes, 'children': 0})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {'head', 'style', 'script', 'title'}:
            self.skip = max(0, self.skip - 1)
            return
        if tag == 'main':
            self.inside = False
            return
        if not self.inside or self.skip or tag in {'html', 'body'} or tag in VOID_TAGS:
            return
        self.output.append('</' + tag + '>')
        if self.stack and self.stack[-1]['tag'] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if self.inside and not self.skip:
            # Keep word separation and explicit BR tags, without transport newlines.
            self.output.append(escape(re.sub(r'[\r\n]+', ' ', data), quote=False))


def discussion_fragment(html):
    parser = FragmentParser(bool(re.search(r'<main(?:\s|>)', html, re.I)))
    parser.feed(html)
    if parser.rawdata or parser.stack or parser.skip:
        raise ReportHTMLInvalid('incomplete_source_html')
    parser.close()
    # Attribute values can contain newlines too; do not let nl2br alter markup.
    return ascii_fragment(''.join(parser.output).replace('\r', '&#13;').replace('\n', '&#10;').strip())
