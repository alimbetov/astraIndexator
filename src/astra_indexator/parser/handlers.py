from __future__ import annotations

import re
import statistics
from typing import Iterable

import pdfplumber
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image

from astra_indexator.acquisition import AcquiredSource

from .base import ParseContext, ParserError
from .model import (
    DocumentElement,
    ElementType,
    OcrCandidate,
    ParsedDocument,
    ParseQuality,
    ParserIdentity,
    QualityStatus,
    SourceGeometry,
    deterministic_element_id,
)


def _eid(source: AcquiredSource, ctx: ParseContext, locator: str, kind: ElementType) -> str:
    return deterministic_element_id(
        document_id=ctx.document_id,
        document_version=ctx.document_version,
        source_sha256=source.sha256,
        detected_format=source.detected_format,
        locator=locator,
        element_type=kind,
    )


def _quality(
    elements: Iterable[DocumentElement],
    candidates: list[OcrCandidate],
    warnings: list[str],
    page_modes=(),
) -> ParseQuality:
    chars = sum(len(e.text or "") for e in elements)
    if candidates:
        status = QualityStatus.OCR_REQUIRED
    elif chars == 0:
        status = QualityStatus.LOW_SIGNAL
    else:
        status = QualityStatus.GOOD
    return ParseQuality(status, chars, len(candidates), tuple(warnings), tuple(page_modes))


def _doc(
    source: AcquiredSource,
    ctx: ParseContext,
    name: str,
    elements: list[DocumentElement],
    candidates: list[OcrCandidate],
    warnings: list[str],
    page_modes=(),
) -> ParsedDocument:
    if len(elements) > ctx.limits.max_elements:
        raise ParserError("PARSER_ELEMENT_LIMIT", "parser element limit exceeded")
    chars = sum(len(e.text or "") for e in elements)
    if chars > ctx.limits.max_extracted_chars:
        raise ParserError("PARSER_TEXT_LIMIT", "parser extracted character limit exceeded")
    return ParsedDocument(
        schema_version="astra-indexator-document-v1",
        document_id=ctx.document_id,
        document_version=ctx.document_version,
        source_sha256=source.sha256,
        detected_format=source.detected_format,
        parser=ParserIdentity(name=name, version="m4-v1", profile=ctx.parser_profile),
        elements=tuple(elements),
        ocr_candidates=tuple(candidates),
        quality=_quality(elements, candidates, warnings, page_modes),
    )


class TextDocumentHandler:
    format_name = "TXT"

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        elements: list[DocumentElement] = []
        current: list[str] = []
        paragraph_index = 0

        def flush() -> None:
            nonlocal paragraph_index
            if not current:
                return
            text = "\n".join(current).strip()
            current.clear()
            if not text:
                return
            elements.append(
                DocumentElement(
                    element_id=_eid(source, context, f"paragraph:{paragraph_index}", ElementType.PARAGRAPH),
                    type=ElementType.PARAGRAPH,
                    order_index=len(elements),
                    text=text,
                    source_locator={"paragraphIndex": paragraph_index},
                )
            )
            paragraph_index += 1
            if len(elements) > context.limits.max_elements:
                raise ParserError("PARSER_ELEMENT_LIMIT", "parser element limit exceeded")
            if sum(len(e.text or "") for e in elements) > context.limits.max_extracted_chars:
                raise ParserError("PARSER_TEXT_LIMIT", "parser extracted character limit exceeded")

        with source.local_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    current.append(line.rstrip("\n"))
                else:
                    flush()
            flush()
        return _doc(source, context, "text-native", elements, [], [])


class MarkdownDocumentHandler:
    format_name = "MARKDOWN"

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        elements: list[DocumentElement] = []
        section_path: list[str] = []
        in_code = False
        code: list[str] = []
        paragraph: list[str] = []

        def emit(kind: ElementType, text: str, locator: str, *, level: int | None = None, metadata=None) -> None:
            elements.append(
                DocumentElement(
                    element_id=_eid(source, context, locator, kind),
                    type=kind,
                    order_index=len(elements),
                    text=text,
                    level=level,
                    section_path=tuple(section_path),
                    source_locator={"lineLocator": locator},
                    metadata=metadata or {},
                )
            )
            if len(elements) > context.limits.max_elements:
                raise ParserError("PARSER_ELEMENT_LIMIT", "parser element limit exceeded")

        def flush_paragraph(line_no: int) -> None:
            if paragraph:
                emit(ElementType.PARAGRAPH, "\n".join(paragraph).strip(), f"paragraph-ending-line:{line_no}")
                paragraph.clear()

        with source.local_path.open("r", encoding="utf-8") as stream:
            lines = enumerate(stream, start=1)
            for line_no, raw in lines:
                line = raw.rstrip("\n")
                if line.strip().startswith("```"):
                    if in_code:
                        emit(ElementType.CODE_BLOCK, "\n".join(code), f"code-ending-line:{line_no}")
                        code.clear()
                        in_code = False
                    else:
                        flush_paragraph(line_no)
                        in_code = True
                    continue
                if in_code:
                    code.append(line)
                    continue
                heading = re.match(r"^(#{1,6})\s+(.+)$", line)
                if heading:
                    flush_paragraph(line_no)
                    level = len(heading.group(1))
                    title = heading.group(2).strip()
                    section_path[:] = section_path[: level - 1]
                    section_path.append(title)
                    emit(ElementType.HEADING, title, f"heading-line:{line_no}", level=level)
                    continue
                item = re.match(r"^\s*((?:[-*+] |\d+[.)]\s+))(.+)$", line)
                if item:
                    flush_paragraph(line_no)
                    emit(
                        ElementType.LIST_ITEM,
                        item.group(2).strip(),
                        f"list-line:{line_no}",
                        metadata={"marker": item.group(1).strip()},
                    )
                    continue
                if not line.strip():
                    flush_paragraph(line_no)
                else:
                    paragraph.append(line)
            flush_paragraph(line_no + 1 if "line_no" in locals() else 1)
        if in_code and code:
            emit(ElementType.CODE_BLOCK, "\n".join(code), "code-eof")
        return _doc(source, context, "markdown-native", elements, [], [])


class DocxDocumentHandler:
    format_name = "DOCX"

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        docx = Document(str(source.local_path))
        elements: list[DocumentElement] = []
        candidates: list[OcrCandidate] = []
        section_path: list[str] = []
        paragraph_index = 0
        table_index = 0

        for child in docx.element.body.iterchildren():
            if isinstance(child, CT_P):
                paragraph = Paragraph(child, docx)
                text = paragraph.text.strip()
                if not text:
                    paragraph_index += 1
                    continue
                style = paragraph.style.name if paragraph.style else ""
                heading = re.match(r"Heading\s+(\d+)", style, re.IGNORECASE)
                list_style = "list" in style.lower()
                if heading:
                    kind = ElementType.HEADING
                    level = int(heading.group(1))
                    section_path[:] = section_path[: level - 1]
                    section_path.append(text)
                elif list_style:
                    kind = ElementType.LIST_ITEM
                    level = None
                else:
                    kind = ElementType.PARAGRAPH
                    level = None
                elements.append(
                    DocumentElement(
                        element_id=_eid(source, context, f"paragraph:{paragraph_index}", kind),
                        type=kind,
                        order_index=len(elements),
                        text=text,
                        level=level,
                        section_path=tuple(section_path),
                        source_locator={"paragraphIndex": paragraph_index, "style": style},
                    )
                )
                paragraph_index += 1
            elif isinstance(child, CT_Tbl):
                table = Table(child, docx)
                table_index += 1
                if table_index > context.limits.max_tables:
                    raise ParserError("PARSER_TABLE_LIMIT", "table limit exceeded")
                rows = [[cell.text for cell in row.cells] for row in table.rows]
                elements.append(
                    DocumentElement(
                        element_id=_eid(source, context, f"table:{table_index - 1}", ElementType.TABLE),
                        type=ElementType.TABLE,
                        order_index=len(elements),
                        section_path=tuple(section_path),
                        source_locator={"tableIndex": table_index - 1},
                        metadata={
                            "rows": rows,
                            "rowCount": len(rows),
                            "columnCount": max((len(row) for row in rows), default=0),
                        },
                    )
                )

        if len(docx.inline_shapes) > context.limits.max_embedded_images:
            raise ParserError("PARSER_IMAGE_LIMIT", "embedded image limit exceeded")
        for image_index, shape in enumerate(docx.inline_shapes):
            locator = f"inline-shape:{image_index}"
            element_id = _eid(source, context, locator, ElementType.IMAGE)
            elements.append(
                DocumentElement(
                    element_id=element_id,
                    type=ElementType.IMAGE,
                    order_index=len(elements),
                    source_locator={"inlineShapeIndex": image_index},
                    role="UNKNOWN",
                    metadata={"widthEmu": int(shape.width), "heightEmu": int(shape.height)},
                )
            )
            candidates.append(
                OcrCandidate(
                    candidate_id=f"ocr:{element_id}",
                    scope="EMBEDDED_IMAGE",
                    page_number=None,
                    reason="docx_embedded_image",
                    element_id=element_id,
                    metadata={"inlineShapeIndex": image_index},
                )
            )
        return _doc(source, context, "docx-native", elements, candidates, [])


class ImageDocumentHandler:
    def __init__(self, format_name: str):
        self.format_name = format_name

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        with Image.open(source.local_path) as image:
            frames = getattr(image, "n_frames", 1)
            if frames > context.limits.max_pages:
                raise ParserError("PARSER_PAGE_LIMIT", "image frame/page limit exceeded")
            elements: list[DocumentElement] = []
            candidates: list[OcrCandidate] = []
            for frame in range(frames):
                image.seek(frame)
                locator = f"frame:{frame + 1}"
                element_id = _eid(source, context, locator, ElementType.IMAGE)
                geometry = SourceGeometry(
                    page_number=frame + 1,
                    page_width=float(image.width),
                    page_height=float(image.height),
                    coordinate_space="pixels",
                )
                elements.append(
                    DocumentElement(
                        element_id,
                        ElementType.IMAGE,
                        len(elements),
                        geometry=geometry,
                        source_locator={"frame": frame + 1},
                        role="SCANNED_PAGE",
                    )
                )
                candidates.append(
                    OcrCandidate(
                        f"ocr:{element_id}", "PAGE", frame + 1, "standalone_image", element_id, geometry
                    )
                )
            return _doc(
                source,
                context,
                f"{self.format_name.lower()}-image-shell",
                elements,
                candidates,
                [],
                ["SCANNED_IMAGE"] * frames,
            )


def _pdf_lines(words: list[dict], *, tolerance: float = 3.0) -> list[dict]:
    ordered = sorted(words, key=lambda word: (float(word.get("top", 0)), float(word.get("x0", 0))))
    groups: list[list[dict]] = []
    for word in ordered:
        top = float(word.get("top", 0))
        if not groups or abs(top - statistics.median(float(w.get("top", 0)) for w in groups[-1])) > tolerance:
            groups.append([word])
        else:
            groups[-1].append(word)

    lines: list[dict] = []
    for group in groups:
        group.sort(key=lambda word: float(word.get("x0", 0)))
        text = " ".join(str(word.get("text", "")) for word in group).strip()
        if not text:
            continue
        sizes = [float(word.get("size", 0) or 0) for word in group]
        fonts = [str(word.get("fontname", "")) for word in group]
        lines.append(
            {
                "words": group,
                "text": text,
                "x0": min(float(word.get("x0", 0)) for word in group),
                "x1": max(float(word.get("x1", 0)) for word in group),
                "top": min(float(word.get("top", 0)) for word in group),
                "bottom": max(float(word.get("bottom", 0)) for word in group),
                "fontSize": statistics.median(sizes) if sizes else 0.0,
                "bold": any("bold" in font.lower() for font in fonts),
            }
        )
    return lines


def _reading_order_v1(lines: list[dict], page_width: float) -> list[dict]:
    if len(lines) < 4:
        return sorted(lines, key=lambda line: (line["top"], line["x0"]))

    full_width = [
        line
        for line in lines
        if (line["x1"] - line["x0"]) >= page_width * 0.62
        or (line["x0"] <= page_width * 0.30 and line["x1"] >= page_width * 0.70)
    ]
    narrow = [line for line in lines if line not in full_width]
    left = [line for line in narrow if (line["x0"] + line["x1"]) / 2 < page_width * 0.46]
    right = [line for line in narrow if (line["x0"] + line["x1"]) / 2 > page_width * 0.54]
    two_columns = len(left) >= 2 and len(right) >= 2
    if not two_columns:
        return sorted(lines, key=lambda line: (line["top"], line["x0"]))

    result: list[dict] = []
    boundaries = sorted(full_width, key=lambda line: line["top"])
    previous_bottom = float("-inf")
    for boundary in boundaries:
        band = [line for line in narrow if previous_bottom <= line["top"] < boundary["top"]]
        result.extend(sorted((line for line in band if line in left), key=lambda line: (line["top"], line["x0"])))
        result.extend(sorted((line for line in band if line in right), key=lambda line: (line["top"], line["x0"])))
        leftovers = [line for line in band if line not in left and line not in right]
        result.extend(sorted(leftovers, key=lambda line: (line["top"], line["x0"])))
        result.append(boundary)
        previous_bottom = boundary["bottom"]

    tail = [line for line in narrow if line["top"] >= previous_bottom]
    result.extend(sorted((line for line in tail if line in left), key=lambda line: (line["top"], line["x0"])))
    result.extend(sorted((line for line in tail if line in right), key=lambda line: (line["top"], line["x0"])))
    result.extend(sorted((line for line in tail if line not in left and line not in right), key=lambda line: (line["top"], line["x0"])))
    return result


def _pdf_element_kind(line: dict, median_font_size: float) -> tuple[ElementType, int | None]:
    text = line["text"]
    if re.match(r"^\s*(?:[-•*+] |\d+[.)]\s+)", text):
        return ElementType.LIST_ITEM, None
    font_size = float(line.get("fontSize", 0.0))
    heading_signal = (
        len(text) <= 180
        and (
            (median_font_size > 0 and font_size >= median_font_size * 1.15)
            or (line.get("bold") and font_size >= median_font_size)
            or bool(re.match(r"^(?:\d+(?:\.\d+)*[.)]?|[A-ZА-ЯӘҒҚҢӨҰҮҺІ][.)])\s+\S+", text))
        )
    )
    return (ElementType.HEADING, 1) if heading_signal else (ElementType.PARAGRAPH, None)


class PdfDocumentHandler:
    format_name = "PDF"

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        elements: list[DocumentElement] = []
        candidates: list[OcrCandidate] = []
        warnings: list[str] = []
        page_modes: list[str] = []
        section_path: list[str] = []
        with pdfplumber.open(str(source.local_path)) as pdf:
            if len(pdf.pages) > context.limits.max_pages:
                raise ParserError("PARSER_PAGE_LIMIT", "PDF page limit exceeded")
            for page_number, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(
                    use_text_flow=False,
                    keep_blank_chars=False,
                    extra_attrs=["fontname", "size"],
                ) or []
                text_chars = sum(len(str(word.get("text", ""))) for word in words)
                images = page.images or []
                page_area = max(float(page.width) * float(page.height), 1.0)
                image_area = sum(
                    max(0.0, float(image.get("x1", 0)) - float(image.get("x0", 0)))
                    * max(0.0, float(image.get("bottom", 0)) - float(image.get("top", 0)))
                    for image in images
                )
                image_ratio = min(image_area / page_area, 1.0)
                if text_chars >= context.quality_profile.min_good_chars_per_page:
                    mode = "MIXED" if images else "NATIVE_TEXT"
                elif images and image_ratio >= context.quality_profile.scanned_image_area_ratio:
                    mode = "SCANNED_IMAGE"
                elif text_chars <= context.quality_profile.low_signal_chars_per_page:
                    mode = "LOW_SIGNAL" if words or images else "EMPTY"
                else:
                    mode = "LOW_SIGNAL"
                page_modes.append(mode)

                lines = _pdf_lines(words)
                ordered_lines = _reading_order_v1(lines, float(page.width))
                font_sizes = [line["fontSize"] for line in lines if line["fontSize"] > 0]
                median_font_size = statistics.median(font_sizes) if font_sizes else 0.0
                for line_index, line in enumerate(ordered_lines):
                    kind, level = _pdf_element_kind(line, median_font_size)
                    text = line["text"]
                    if kind is ElementType.HEADING:
                        section_path[:] = section_path[:0]
                        section_path.append(text)
                    role = None
                    if line["top"] <= float(page.height) * 0.08:
                        role = "PAGE_HEADER_CANDIDATE"
                    elif line["bottom"] >= float(page.height) * 0.92:
                        role = "PAGE_FOOTER_CANDIDATE"
                    if re.fullmatch(r"(?:page\s+)?\d+(?:\s+(?:of|/)\s+\d+)?", text, re.IGNORECASE):
                        role = "PAGE_NUMBER"
                    locator = f"page:{page_number}:line:{line_index}"
                    geometry = SourceGeometry(
                        page_number,
                        line["x0"],
                        line["top"],
                        line["x1"],
                        line["bottom"],
                        float(page.width),
                        float(page.height),
                        "pdf-points-top-left",
                    )
                    elements.append(
                        DocumentElement(
                            _eid(source, context, locator, kind),
                            kind,
                            len(elements),
                            text=text,
                            level=level,
                            geometry=geometry,
                            section_path=tuple(section_path),
                            source_locator={"pageNumber": page_number, "lineIndex": line_index},
                            style_hints={"fontSize": line["fontSize"], "bold": line["bold"]},
                            role=role,
                        )
                    )

                if len(images) > context.limits.max_embedded_images:
                    raise ParserError("PARSER_IMAGE_LIMIT", "embedded image limit exceeded")
                for image_index, image in enumerate(images):
                    locator = f"page:{page_number}:image:{image_index}"
                    geometry = SourceGeometry(
                        page_number,
                        float(image.get("x0", 0)),
                        float(image.get("top", 0)),
                        float(image.get("x1", 0)),
                        float(image.get("bottom", 0)),
                        float(page.width),
                        float(page.height),
                        "pdf-points-top-left",
                    )
                    element_id = _eid(source, context, locator, ElementType.IMAGE)
                    elements.append(
                        DocumentElement(
                            element_id,
                            ElementType.IMAGE,
                            len(elements),
                            geometry=geometry,
                            source_locator={"pageNumber": page_number, "imageIndex": image_index},
                            role="UNKNOWN",
                        )
                    )
                    candidates.append(
                        OcrCandidate(
                            f"ocr:{element_id}",
                            "EMBEDDED_IMAGE",
                            page_number,
                            "embedded_image",
                            element_id,
                            geometry,
                        )
                    )

                if mode in {"SCANNED_IMAGE", "LOW_SIGNAL"}:
                    candidates.append(
                        OcrCandidate(
                            f"ocr-page:{page_number}",
                            "PAGE",
                            page_number,
                            mode.lower(),
                            metadata={"pageMode": mode},
                        )
                    )
                elements.append(
                    DocumentElement(
                        _eid(source, context, f"page:{page_number}:break", ElementType.PAGE_BREAK),
                        ElementType.PAGE_BREAK,
                        len(elements),
                        source_locator={"pageNumber": page_number},
                    )
                )
                if len(elements) > context.limits.max_elements:
                    raise ParserError("PARSER_ELEMENT_LIMIT", "parser element limit exceeded")
        return _doc(source, context, "pdfplumber-layout", elements, candidates, warnings, page_modes)
