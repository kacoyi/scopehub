"""Select authored prose from exact Markdown source spans, without rendering HTML."""

import re

from marko import Markdown, block, inline


QUOTE_END = r"""["'”’»」』）)]*"""
SENTENCE_END = re.compile(rf'[。！？]{QUOTE_END}|[.!?]{QUOTE_END}(?=\s|$)')
UNBALANCED_MARKER = re.compile(r'(?<![\w\\])(?:`|[*_]{1,3})(?=\S)')
TEXT = (inline.RawText, inline.Literal)
FORMATTING = (inline.StrongEmphasis, inline.Emphasis)
PROSE_INLINE = (*TEXT, inline.LineBreak, inline.CodeSpan)
ATOMIC_INLINE = (*FORMATTING, inline.CodeSpan)


def terminal_body(body):
    # The terminal retains line wraps and replaces Markdown list bullets.
    # Undo that layout before asking CommonMark to classify the reply.
    lines = (line.removeprefix('  ') for line in body.splitlines())
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


def sentences(paragraph, source, *, skip_first):
    nodes = paragraph.children
    if len(nodes) > 1 and isinstance(nodes[0], FORMATTING) and isinstance(nodes[1], inline.LineBreak):
        # A decoration-only opening line can appear before prose in the same paragraph.
        nodes = nodes[2:]
    if not nodes or nodes[0].source_span is None:
        return
    origin = cursor = nodes[0].start_pos
    mask, visible = [], []
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
            yield ' '.join(source[origin + start:origin + stop].split())
        start = stop


def body_passages(body, *, rendered):
    source = terminal_body(body) if rendered else body
    source = source.replace('\r\n', '\n').replace('\r', '\n').replace('\0', '\ufffd')
    try:
        document = Markdown(extensions=['gfm']).parse(source)
        for paragraph, starts_item in paragraphs(document):
            yield from sentences(paragraph, source, skip_first=rendered and starts_item)
    except RecursionError:
        # Excessively nested input supplies no structurally verifiable observation.
        return
