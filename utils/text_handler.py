import logging
import re
from typing import List, Tuple, Optional
from bs4 import Comment
import tiktoken
import html2text
# from langdetect import detect

# def detect_language(entry):
#     title = entry.get("title")
#     original_content = entry.get("content")
#     content = (
#         original_content[0].get("value")
#         if original_content
#         else entry.get("summary")
#     )
#     text =f"{title} {content}"
#     source_language = "auto"
#     try:
#         source_language = detect(text)
#     except Exception as e:
#         logging.warning("Cannot detect source language:%s,%s", e, text)

#     return source_language


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
def tokenize(text: str) -> List[str]:
    encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
    return encoding.encode(text)


def combine_chunks_with_no_minimum(
    chunks: List[str],
    max_tokens: int,
    chunk_delimiter="\n",
    header: Optional[str] = None,
    add_ellipsis_for_overflow=False,
) -> Tuple[List[str], List[int]]:
    """
    This function combines text chunks into larger blocks without exceeding a specified token count.
    It returns the combined text blocks, their original indices, and the count of chunks dropped due to overflow.
    """
    dropped_chunk_count = 0
    output = []  # list to hold the final combined chunks
    output_indices = []  # list to hold the indices of the final combined chunks
    candidate = (
        [] if header is None else [header]
    )  # list to hold the current combined chunk candidate
    candidate_indices = []
    for chunk_i, chunk in enumerate(chunks):
        chunk_with_header = [chunk] if header is None else [header, chunk]
        if len(tokenize(chunk_delimiter.join(chunk_with_header))) > max_tokens:
            logging.warning("chunk overflow")
            if (
                add_ellipsis_for_overflow
                and len(tokenize(chunk_delimiter.join(candidate + ["..."])))
                <= max_tokens
            ):
                candidate.append("...")
                dropped_chunk_count += 1
            continue  # this case would break downstream assumptions
        # estimate token count with the current chunk added
        extended_candidate_token_count = len(
            tokenize(chunk_delimiter.join(candidate + [chunk]))
        )
        # If the token count exceeds max_tokens, add the current candidate to output and start a new candidate
        if extended_candidate_token_count > max_tokens:
            output.append(chunk_delimiter.join(candidate))
            output_indices.append(candidate_indices)
            candidate = chunk_with_header  # re-initialize candidate
            candidate_indices = [chunk_i]
        # otherwise keep extending the candidate
        else:
            candidate.append(chunk)
            candidate_indices.append(chunk_i)
    # add the remaining candidate to output if it's not empty
    if (header is not None and len(candidate) > 1) or (
        header is None and len(candidate) > 0
    ):
        output.append(chunk_delimiter.join(candidate))
        output_indices.append(candidate_indices)
    return output, output_indices, dropped_chunk_count


def chunk_on_delimiter(
    input_string: str, max_tokens: int, delimiter: str = " "
) -> List[str]:
    """
    This function chunks a text into smaller pieces based on a maximum token count and a delimiter.
    """
    chunks = input_string.split(delimiter)
    combined_chunks, _, dropped_chunk_count = combine_chunks_with_no_minimum(
        chunks, max_tokens, chunk_delimiter=delimiter, add_ellipsis_for_overflow=True
    )
    if dropped_chunk_count > 0:
        logging.warning("%d chunks were dropped due to overflow", dropped_chunk_count)
    combined_chunks = [f"{chunk}{delimiter}" for chunk in combined_chunks]
    return combined_chunks


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
