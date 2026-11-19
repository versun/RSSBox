import functools
import html as html_lib
import math
import re
from typing import List
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
import tiktoken
import html2text


def clean_content(content: str | None) -> str:
    """convert html to markdown without useless tags; None becomes "" """
    if content is None:
        return ""

    h = html2text.HTML2Text()
    h.decode_errors = "ignore"
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_tables = True
    h.ignore_emphasis = True
    h.single_line_break = True
    h.no_wrap_links = True
    h.mark_code = True
    h.unicode_snob = True
    h.body_width = 0
    h.drop_white_space = True
    h.ignore_mailto_links = True

    # content = h.handle(h.handle(content)) #remove all \n
    content = h.handle(content)
    content = re.sub(r"\n\s*\n", "\n", content)
    return content


# Thanks to https://github.com/openai/openai-cookbook/blob/main/examples/Summarizing_with_controllable_detail.ipynb
@functools.lru_cache(maxsize=1024)
def tokenize(text: str) -> List[int]:
    """Tokenize text with caching for frequent inputs"""
    encoding = tiktoken.encoding_for_model("gpt-4o")
    return encoding.encode(text)


def get_token_count(text: str) -> int:
    """Get token count with caching"""
    return len(tokenize(text))


def split_large_sentence(
    sentence: str, max_tokens: int, delimiters: List[str] = [",", ";", " "]
) -> List[str]:
    """
    Split a sentence that exceeds max_tokens using fallback delimiters
    Returns list of chunks with trailing delimiters preserved
    """
    if get_token_count(sentence) <= max_tokens:
        return [sentence]

    chunks = []
    current_chunk = ""
    current_token_count = 0
    encoding = tiktoken.encoding_for_model("gpt-4o")

    # 尝试按优先级使用不同的分隔符
    for delimiter in delimiters:
        parts = sentence.split(delimiter)
        if len(parts) > 1:  # 找到有效的分隔符
            for i, part in enumerate(parts):
                if not part.strip():
                    continue

                # 添加分隔符（除了最后一部分）
                segment = part + delimiter if i < len(parts) - 1 else part
                segment_tokens = get_token_count(segment)

                # 如果当前块加上新段落会超过限制，则完成当前块
                if current_token_count + segment_tokens > max_tokens and current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = segment
                    current_token_count = segment_tokens
                else:
                    current_chunk += segment
                    current_token_count += segment_tokens

            # 添加最后一块
            if current_chunk:
                chunks.append(current_chunk)

            # 递归处理仍然过大的块
            final_chunks = []
            for chunk in chunks:
                if get_token_count(chunk) > max_tokens:
                    # 使用更细的分隔符递归分割
                    final_chunks.extend(
                        split_large_sentence(
                            chunk,
                            max_tokens,
                            delimiters=delimiters[1:] if len(delimiters) > 1 else [],
                        )
                    )
                else:
                    final_chunks.append(chunk)
            return final_chunks

    # 没有找到合适的分隔符 - 按token数硬分割
    tokens = tokenize(sentence)
    chunks = []
    for i in range(0, len(tokens), max_tokens):
        chunk_tokens = tokens[i : i + max_tokens]
        chunks.append(encoding.decode(chunk_tokens))
    return chunks


def chunk_on_delimiter(
    input_string: str,
    max_tokens: int,
    delimiter: str = ".",
    fallback_delimiters: List[str] = ["!", "?", "\n", ";", "。", "！", "？"],
) -> List[str]:
    """
    Chunk text into segments not exceeding max_tokens, preserving natural boundaries.

    Args:
        input_string: Text to chunk
        max_tokens: Maximum tokens per chunk
        delimiter: Primary sentence delimiter
        fallback_delimiters: Secondary delimiters for splitting long sentences

    Returns:
        List of text chunks
    """
    # 空输入处理
    if not input_string.strip():
        return [""]

    # 第一步：按主分隔符分割
    sentences = []
    current_sentence = ""

    for char in input_string:
        current_sentence += char
        if char in delimiter + "".join(fallback_delimiters):
            sentences.append(current_sentence)
            current_sentence = ""

    # 添加最后一句
    if current_sentence:
        sentences.append(current_sentence)

    # 第二步：处理过长的句子
    chunks = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        token_count = get_token_count(sentence)

        if token_count <= max_tokens:
            chunks.append(sentence)
        else:
            # 长句子需要进一步分割
            chunks.extend(
                split_large_sentence(
                    sentence, max_tokens, delimiters=fallback_delimiters
                )
            )

    # 第三步：组合小片段，确保不超过max_tokens
    combined_chunks = []
    current_chunk = ""
    current_token_count = 0

    for chunk in chunks:
        chunk_token_count = get_token_count(chunk)

        # 如果当前块为空，直接添加
        if not current_chunk:
            current_chunk = chunk
            current_token_count = chunk_token_count
            continue

        # 检查组合后是否超过限制
        if current_token_count + chunk_token_count <= max_tokens:
            current_chunk += (
                " " + chunk if not current_chunk.endswith((" ", "\n")) else chunk
            )
            current_token_count += chunk_token_count
        else:
            combined_chunks.append(current_chunk)
            current_chunk = chunk
            current_token_count = chunk_token_count

    # 添加最后一个块
    if current_chunk:
        combined_chunks.append(current_chunk)

    return combined_chunks


def adaptive_chunking(
    text: str,
    target_chunks: int,
    min_chunk_size: int = 200,
    max_chunk_size: int = 1500,
    initial_delimiter: str = ".",
) -> List[str]:
    """
    Adaptive chunking that adjusts to hit target chunk count

    Args:
        text: Text to chunk
        target_chunks: Desired number of chunks
        min_chunk_size: Minimum token size per chunk
        max_chunk_size: Maximum token size per chunk

    Returns:
        List of text chunks
    """
    total_tokens = get_token_count(text)

    # 计算理想块大小
    ideal_size = total_tokens // target_chunks
    chunk_size = max(min_chunk_size, min(max_chunk_size, ideal_size))

    # 初始分块
    chunks = chunk_on_delimiter(text, chunk_size, delimiter=initial_delimiter)

    # 调整块数量
    if len(chunks) < target_chunks * 0.5:
        # 块太少 - 尝试减小块大小
        return chunk_on_delimiter(text, max(min_chunk_size, int(chunk_size * 0.7)))
    elif len(chunks) > target_chunks * 1.5:
        # 块太多 - 尝试增大块大小
        return chunk_on_delimiter(text, min(max_chunk_size, int(chunk_size * 1.3)))

    return chunks


_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")
_HTML_CODE_TAGS = frozenset({"pre", "code"})
_HTML_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "aside",
        "header",
        "footer",
        "blockquote",
        "figure",
        "figcaption",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "hr",
    }
)


_HTML_TAG_SOUP = BeautifulSoup("", "html.parser")


def _open_close_tags(tag: Tag, strip_id: bool = False) -> tuple[str, str]:
    attrs = {k: v for k, v in tag.attrs.items() if not (strip_id and k == "id")}
    clone = _HTML_TAG_SOUP.new_tag(tag.name, attrs=attrs)
    rendered = str(clone)
    close_tag = f"</{tag.name}>"
    if rendered.endswith(close_tag):
        return rendered[: -len(close_tag)], close_tag
    return rendered, ""


def _pack_fragments(fragments: List[str], max_tokens: int, sep: str = "") -> List[str]:
    packed: List[str] = []
    current_parts: List[str] = []
    current_tokens = 0
    sep_tokens = get_token_count(sep) if sep else 0

    for fragment in fragments:
        if not fragment:
            continue
        frag_tokens = get_token_count(fragment)
        if not current_parts:
            current_parts = [fragment]
            current_tokens = frag_tokens
            continue

        # Fast O(1) accumulation while clearly below the budget; verify exact
        # joined token count near the boundary to keep packing exact and O(N).
        if current_tokens + sep_tokens + frag_tokens <= max_tokens - 2:
            current_parts.append(fragment)
            current_tokens += sep_tokens + frag_tokens
            continue

        current_str = sep.join(current_parts)
        candidate = f"{current_str}{sep}{fragment}"
        candidate_tokens = get_token_count(candidate)
        if candidate_tokens <= max_tokens:
            current_parts = [candidate]
            current_tokens = candidate_tokens
        else:
            packed.append(current_str)
            current_parts = [fragment]
            current_tokens = frag_tokens

    if current_parts:
        packed.append(sep.join(current_parts))
    return packed


def _split_raw_for_escaped_budget(raw: str, max_tokens: int) -> List[str]:
    """Split raw text so every HTML-escaped piece fits within ``max_tokens``
    without ever cutting an HTML entity in half."""
    escaped = html_lib.escape(raw, quote=False)
    if get_token_count(escaped) <= max_tokens or len(raw) <= 1:
        return [escaped]

    mid = len(raw) // 2
    split_at = raw.rfind(" ", 1, mid + 1)
    if split_at == -1:
        split_at = raw.find(" ", mid)
    if split_at <= 0 or split_at >= len(raw) - 1:
        split_at = mid
    else:
        split_at += 1  # Keep trailing space on the left fragment.

    return _split_raw_for_escaped_budget(
        raw[:split_at], max_tokens
    ) + _split_raw_for_escaped_budget(raw[split_at:], max_tokens)


def _split_text_to_escaped_fragments(
    raw_text: str,
    max_tokens: int,
    *,
    preserve_newlines: bool = False,
) -> List[str]:
    if preserve_newlines:
        pieces: List[str] = []
        for line in raw_text.splitlines(keepends=True):
            pieces.extend(_split_raw_for_escaped_budget(line, max_tokens))
        return _pack_fragments(pieces, max_tokens, sep="")

    leading_space = " " if raw_text and raw_text[0].isspace() else ""
    trailing_space = " " if raw_text and raw_text[-1].isspace() else ""
    raw_chunks = [c for c in chunk_on_delimiter(raw_text, max_tokens) if c]
    if not raw_chunks:
        return []
    if leading_space and not raw_chunks[0][0].isspace():
        raw_chunks[0] = leading_space + raw_chunks[0]
    if trailing_space and not raw_chunks[-1][-1].isspace():
        raw_chunks[-1] = raw_chunks[-1] + trailing_space

    pieces = []
    for chunk in raw_chunks:
        pieces.extend(_split_raw_for_escaped_budget(chunk, max_tokens))
    return pieces


def _group_code_lines(fragments: List[str], max_tokens: int) -> List[str]:
    """Coalesce inline fragments belonging to the same source line so code
    chunks split cleanly on line boundaries whenever a line fits the budget."""
    grouped: List[str] = []
    line_parts: List[str] = []

    def _flush_line():
        if not line_parts:
            return
        line = "".join(line_parts)
        if get_token_count(line) <= max_tokens:
            grouped.append(line)
        else:
            grouped.extend(line_parts)
        line_parts.clear()

    for frag in fragments:
        line_parts.append(frag)
        if frag.endswith(("\n", "\r")):
            _flush_line()
    _flush_line()
    return grouped


def _split_html_node(
    node, max_tokens: int, *, preserve_newlines: bool = False
) -> List[str]:
    if isinstance(node, Comment):
        return []

    if isinstance(node, NavigableString):
        raw = str(node)
        if not raw or (not preserve_newlines and not raw.strip()):
            return []
        escaped = html_lib.escape(raw, quote=False)
        if get_token_count(escaped) <= max_tokens:
            return [escaped]
        return _split_text_to_escaped_fragments(
            raw, max_tokens, preserve_newlines=preserve_newlines
        )

    rendered = str(node)
    if not rendered or (not preserve_newlines and not rendered.strip()):
        return []
    if get_token_count(rendered) <= max_tokens:
        return [rendered]

    if not isinstance(node, Tag):
        return chunk_on_delimiter(rendered, max_tokens)

    open_tag, close_tag = _open_close_tags(node, strip_id=False)
    open_tag_no_id = (
        _open_close_tags(node, strip_id=True)[0] if "id" in node.attrs else open_tag
    )
    wrapper_overhead = max(
        get_token_count(open_tag + close_tag),
        get_token_count(open_tag) + get_token_count(close_tag),
    )
    inner_budget = max_tokens - wrapper_overhead

    if inner_budget <= 1 or not close_tag:
        # Void tags (e.g. <img ...>) or tags whose opening wrapper alone eats
        # the budget are atomic markup; never slice through tag syntax with
        # plain-text delimiter splitting.
        return [rendered]

    children = [c for c in node.children if not isinstance(c, Comment)]
    if not children:
        return [rendered]

    in_code = preserve_newlines or (node.name in _HTML_CODE_TAGS)
    has_tag_child = any(isinstance(c, Tag) for c in children)
    if in_code and not has_tag_child:
        pieces = _split_text_to_escaped_fragments(
            node.get_text(), inner_budget, preserve_newlines=True
        )
        return [
            f"{open_tag if idx == 0 else open_tag_no_id}{piece}{close_tag}"
            for idx, piece in enumerate(pieces)
            if piece
        ]

    has_block_child = not in_code and any(
        isinstance(c, Tag) and c.name in _HTML_BLOCK_TAGS for c in children
    )
    child_fragments: List[str] = []
    pending_code_indent = ""
    for child in children:
        if isinstance(child, NavigableString):
            raw_child = str(child)
            if not raw_child:
                continue
            if not raw_child.strip():
                if in_code:
                    # Keep newline on the preceding line and attach leading
                    # indentation to the next line's first fragment so chunk
                    # splits never detach indentation from its code line.
                    for line in raw_child.splitlines(keepends=True):
                        if line.endswith(("\n", "\r")):
                            if pending_code_indent:
                                child_fragments.append(pending_code_indent + line)
                                pending_code_indent = ""
                            else:
                                child_fragments.append(line)
                        else:
                            pending_code_indent += line
                elif not has_block_child:
                    child_fragments.append(raw_child)
                continue
            if in_code:
                for line in raw_child.splitlines(keepends=True):
                    if not line.strip() and not line.endswith(("\n", "\r")):
                        pending_code_indent += line
                        continue
                    piece_raw = pending_code_indent + line
                    pending_code_indent = ""
                    child_fragments.extend(
                        _split_raw_for_escaped_budget(piece_raw, inner_budget)
                    )
                continue
            child_fragments.extend(
                _split_html_node(child, inner_budget, preserve_newlines=False)
            )
            continue

        child_str = str(child)
        if not child_str:
            continue
        if not child_str.strip():
            if in_code or not has_block_child:
                child_fragments.append(child_str)
            continue
        sub_frags = _split_html_node(child, inner_budget, preserve_newlines=in_code)
        if pending_code_indent and sub_frags:
            prefixed = pending_code_indent + sub_frags[0]
            if get_token_count(prefixed) <= inner_budget:
                sub_frags[0] = prefixed
            else:
                child_fragments.append(pending_code_indent)
            pending_code_indent = ""
        child_fragments.extend(sub_frags)

    if pending_code_indent:
        child_fragments.append(pending_code_indent)

    if not child_fragments:
        return []

    if in_code:
        child_fragments = _group_code_lines(child_fragments, inner_budget)

    sep = "\n" if has_block_child else ""
    grouped = _pack_fragments(child_fragments, inner_budget, sep=sep)
    return [
        f"{open_tag if idx == 0 else open_tag_no_id}{group}{close_tag}"
        for idx, group in enumerate(grouped)
        if group.strip()
    ]


def chunk_html(html: str, max_tokens: int) -> List[str]:
    """Split HTML content into valid chunks under ``max_tokens``.

    Preserves HTML tags, links, code blocks, and ``notranslate`` spans by
    grouping sibling DOM nodes instead of slicing on punctuation. Falls back
    to plain-text chunking when the input contains no HTML markup.
    """
    if not html or not html.strip():
        return [""]
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")

    total_tokens = get_token_count(html)
    if total_tokens <= max_tokens:
        return [html]

    if not _HTML_TAG_RE.search(html):
        return adaptive_chunking(
            html,
            target_chunks=max(1, math.ceil(total_tokens / max_tokens)),
            min_chunk_size=min(500, max_tokens),
            max_chunk_size=max_tokens,
        )

    soup = BeautifulSoup(html, "lxml")
    root = soup.body if soup.body else soup

    fragments: List[str] = []
    for child in root.children:
        if isinstance(child, Comment):
            continue
        if not str(child).strip():
            continue
        fragments.extend(_split_html_node(child, max_tokens))

    if not fragments:
        return [html]

    return _pack_fragments(fragments, max_tokens, sep="\n")


def should_skip(element):
    skip_tags = [
        "pre",
        "code",
        "script",
        "style",
        "head",
        "title",
        "meta",
        "abbr",
        "address",
        "samp",
        "kbd",
        "bdo",
        "cite",
        "dfn",
        "iframe",
    ]
    if isinstance(element, Comment):
        return True
    if element.find_parents(skip_tags):
        return True

    # check if the element class is katex for MathMl
    if element.find_parent("span", class_="katex"):
        return True

    # 使用正则表达式来检查元素是否为数字、URL、电子邮件或包含特定符号
    skip_patterns = [
        r"^http",  # URL
        r"^[^@]+@[^@]+\.[^@]+$",  # 电子邮件
        r"^[\d\W]+$",  # 纯数字或者数字和符号的组合
    ]

    for pattern in skip_patterns:
        if re.match(pattern, element.get_text(strip=True)):
            return True

    return False


def unwrap_tags(soup) -> str:
    tags_to_unwrap = [
        "i",
        "a",
        "strong",
        "b",
        "em",
        "span",
        "sup",
        "sub",
        "mark",
        "del",
        "ins",
        "u",
        "s",
        "small",
    ]
    for tag_name in tags_to_unwrap:
        for tag in soup.find_all(tag_name):
            tag.unwrap()
    return str(soup)


def set_translation_display(
    original: str, translation: str, translation_display: int, seprator: str = " || "
) -> str:
    if translation_display == 0:  #'Only Translation'
        return translation
    elif translation_display == 1:  #'Translation || Original'
        return f"{translation}{seprator}{original}"
    elif translation_display == 2:  #'Original || Translation'
        return f"{original}{seprator}{translation}"
    else:
        return ""
