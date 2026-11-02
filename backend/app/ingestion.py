from dataclasses import dataclass
from io import BytesIO
import re

from pypdf import PdfReader


class IngestionError(Exception):
    def __init__(self, code: str, retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass
class TextPart:
    text: str
    page: int | None = None
    section: str = ''
    line_start: int | None = None
    line_end: int | None = None


def parse_and_chunk(data: bytes, suffix: str, size=800, overlap=120) -> list[TextPart]:
    if not 0 <= overlap < size:
        raise ValueError('Invalid chunk size')
    parts = []
    if suffix == '.md':
        try:
            lines = data.decode('utf-8-sig').splitlines()
        except UnicodeDecodeError as exc:
            raise IngestionError('INVALID_UTF8') from exc
        section = ''
        start = 1
        paragraph = []

        def flush(end):
            nonlocal paragraph
            text = '\n'.join(paragraph).strip()
            if text:
                parts.append(TextPart(text, section=section, line_start=start, line_end=end))
            paragraph = []

        for number, line in enumerate(lines, 1):
            if re.match(r'^#{1,6}\s+', line):
                flush(number - 1)
                section = re.sub(r'^#{1,6}\s+', '', line).strip()[:200]
                start = number
                paragraph = [line]
            elif not line.strip():
                flush(number - 1)
                start = number + 1
            else:
                if not paragraph:
                    start = number
                paragraph.append(line)
        flush(len(lines))
    elif suffix == '.pdf':
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise IngestionError('ENCRYPTED_PDF')
            if len(reader.pages) > 200:
                raise IngestionError('TOO_MANY_PAGES')
            for number, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                if content is not None and len(content.get_data()) > 8 * 1024 * 1024:
                    raise IngestionError('PDF_PAGE_TOO_LARGE')
                text = (page.extract_text() or '').strip()
                if text:
                    parts.append(TextPart(text, page=number))
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError('INVALID_PDF') from exc
    else:
        raise IngestionError('UNSUPPORTED_TYPE')
    if not parts:
        raise IngestionError('NO_TEXT')
    if sum(len(part.text) for part in parts) > 200_000:
        raise IngestionError('TEXT_TOO_LARGE')
    result = []
    for part in parts:
        for offset in range(0, len(part.text), size-overlap):
            text = part.text[offset:offset+size].strip()
            if text:
                result.append(TextPart(text, part.page, part.section, part.line_start, part.line_end))
                if len(result) > 1000:
                    raise IngestionError('TOO_MANY_CHUNKS')
            if offset + size >= len(part.text):
                break
    return result
