"""Select authored prose from exact Markdown source spans, without rendering HTML."""

import re

from marko import Markdown, block, inline


QUOTE_END = r"""["'”’»」』）)]*"""
SENTENCE_END = re.compile(rf'[。！？]{QUOTE_END}|[.!?]{QUOTE_END}(?=\s|$)')
UNBALANCED_MARKER = re.compile(r'(?<![\w\\])(?:`|[*_]{1,3})(?=\S)')
PROSE_WHITESPACE = re.compile(r'\s+')
TEXT = (inline.RawText, inline.Literal)
FORMATTING = (inline.StrongEmphasis, inline.Emphasis)
PROSE_INLINE = (*TEXT, inline.LineBreak, inline.CodeSpan)
ATOMIC_INLINE = (*FORMATTING, inline.CodeSpan)


def terminal_body(body):
    # The terminal retains line wraps and replaces Markdown list bullets.
    # Undo that layout before asking CommonMark to classify the reply.
    lines = (line.removeprefix('  ') for line in body.split('\n'))
    return '\n'.join('- ' + line[2:] if line.startswith('• ') else line for line in lines)


def paragraphs(container):
    for index, child in enumerate(container.children):
        if isinstance(child, block.Paragraph):
            yield child, index == 0 and isinstance(container, block.ListItem)
        elif isinstance(child, (block.List, block.ListItem)):
            yield from paragraphs(child)


def is_prose_inline(node):
    if isinstance(node, FORMATTING):
        return all(is_prose_inline(child) for child in node.children)
    return isinstance(node, PROSE_INLINE)


def emphasis_terminator(node, source):
    while isinstance(node, FORMATTING) and node.children:
        node = node.children[-1]
    if isinstance(node, TEXT) and node.source_span is not None:
        text = source[node.start_pos:node.end_pos]
        return next((end[0][0] for end in SENTENCE_END.finditer(text) if end.end() == len(text)), '')
    return ''


def literal_spans(node):
    if isinstance(node, (inline.CodeSpan, block.CodeBlock, block.FencedCode)):
        yield node
    elif isinstance(children := getattr(node, 'children', None), list):
        for child in children:
            yield from literal_spans(child)


def code_spelling(node, source):
    raw = source[node.start_pos:node.end_pos]
    if '\n' not in raw:
        return raw
    # Use CommonMark's logical code text across a line break so container
    # indentation is not added to a filename. Keep the authored delimiters
    # and any single boundary padding that the parser removed.
    (opening, content_start), (content_end, closing) = node.syntax_spans
    content = node.children
    if content.strip() and source[content_start] in ' \n' and source[content_end - 1] in ' \n':
        content = f' {content} '
    return source[opening:content_start] + content + source[content_end:closing]


def project_text(source, start, stop, literals):
    pieces = []
    for node in literals:
        if node.start_pos >= stop:
            break
        if node.end_pos <= start:
            continue
        literal = (code_spelling(node, source) if isinstance(node, inline.CodeSpan)
                   else source[node.start_pos:node.end_pos].rstrip('\n'))
        pieces.extend((PROSE_WHITESPACE.sub(' ', source[start:node.start_pos]), literal))
        start = node.end_pos
    pieces.append(PROSE_WHITESPACE.sub(' ', source[start:stop]))
    return ''.join(pieces).strip()


def sentences(paragraph, source, *, skip_first):
    nodes = paragraph.children
    if len(nodes) > 1 and isinstance(nodes[0], FORMATTING) and isinstance(nodes[1], inline.LineBreak):
        # A decoration-only opening line can appear before prose in the same paragraph.
        nodes = nodes[2:]
    if not nodes or nodes[0].source_span is None:
        return
    origin = cursor = nodes[0].start_pos
    mask, visible, code_spans = [], [], []
    for node in nodes:
        if node.source_span is None or not is_prose_inline(node):
            # Citations and other non-prose end the selectable part of this paragraph.
            break
        start, end = node.source_span
        gap = source[cursor:start]
        if start < cursor or gap.strip():
            break
        raw = source[start:end]
        punctuation = raw
        plaintext = raw
        if isinstance(node, ATOMIC_INLINE):
            plaintext = '\0' * len(raw)
            mark = emphasis_terminator(node, source)
            punctuation = plaintext[:-1] + mark if mark else plaintext
        code_spans.extend(literal_spans(node))
        mask.extend((gap, punctuation))
        visible.extend((gap, plaintext))
        cursor = end

    visible_text = ''.join(visible)
    start = 0
    for index, boundary in enumerate(SENTENCE_END.finditer(''.join(mask))):
        stop = boundary.end()
        plaintext = visible_text[start:stop]
        if (not (skip_first and index == 0) and any(character.isalpha() for character in plaintext)
                and not UNBALANCED_MARKER.search(plaintext)):
            left, right = origin + start, origin + stop
            yield left, right, project_text(source, left, right, code_spans)
        start = stop


def markdown_source(body):
    return body.replace('\r\n', '\n').replace('\r', '\n').replace('\0', '\ufffd')


def passage_spans(source, *, rendered, document=None):
    try:
        if document is None:
            document = Markdown(extensions=['gfm']).parse(source)
        for paragraph, starts_item in paragraphs(document):
            yield from sentences(paragraph, source, skip_first=rendered and starts_item)
    except RecursionError:
        # Excessively nested input supplies no structurally verifiable observation.
        return


def response_body(body, *, rendered):
    source = markdown_source(body)
    if rendered:
        source = terminal_body(source)
    try:
        document = Markdown(extensions=['gfm']).parse(source)
        projection = project_text(source, 0, len(source), literal_spans(document))
    except RecursionError:
        return '', ()
    observations = (passage for _, _, passage in passage_spans(source, rendered=rendered, document=document))
    return projection, observations


def body_passages(body, *, rendered):
    return response_body(body, rendered=rendered)[1]


def standalone_passage(text):
    source = markdown_source(text).strip()
    for start, stop, passage in passage_spans(source, rendered=False):
        if start == 0 and stop == len(source):
            return passage
    return None
