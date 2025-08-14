import functools
import re
from typing import List, Tuple, Optional
from bs4 import Comment
import tiktoken
import html2text


def clean_content(content: str) -> str:
    """convert html to markdown without useless tags"""
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
