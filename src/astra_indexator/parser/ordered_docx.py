from __future__ import annotations

import re

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from astra_indexator.acquisition import AcquiredSource

from .base import ParseContext, ParserError
from .handlers import _doc, _eid
from .model import DocumentElement, ElementType, OcrCandidate, ParsedDocument


class OrderedDocxDocumentHandler:
    """DOCX handler preserving body order at paragraph/table/image granularity.

    Inline images are emitted while traversing the body paragraph that owns the
    drawing instead of being appended after all textual content. This keeps the
    canonical sequence suitable for later OCR reconciliation.
    """

    format_name = "DOCX"

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        docx = Document(str(source.local_path))
        elements: list[DocumentElement] = []
        candidates: list[OcrCandidate] = []
        section_path: list[str] = []
        paragraph_index = 0
        table_index = 0
        inline_shapes = list(docx.inline_shapes)
        if len(inline_shapes) > context.limits.max_embedded_images:
            raise ParserError("PARSER_IMAGE_LIMIT", "embedded image limit exceeded")
        image_index = 0

        def emit_inline_images(child: CT_P, owning_paragraph_index: int) -> None:
            nonlocal image_index
            drawings = child.xpath(".//w:drawing")
            for drawing_index, _ in enumerate(drawings):
                if image_index >= len(inline_shapes):
                    raise ParserError(
                        "PARSER_DOCX_IMAGE_MAPPING",
                        "DOCX drawing/inline-shape mapping is inconsistent",
                    )
                shape = inline_shapes[image_index]
                locator = f"paragraph:{owning_paragraph_index}:inline-shape:{drawing_index}"
                element_id = _eid(source, context, locator, ElementType.IMAGE)
                elements.append(
                    DocumentElement(
                        element_id=element_id,
                        type=ElementType.IMAGE,
                        order_index=len(elements),
                        section_path=tuple(section_path),
                        source_locator={
                            "paragraphIndex": owning_paragraph_index,
                            "inlineShapeIndex": image_index,
                            "drawingIndex": drawing_index,
                        },
                        role="EMBEDDED_IMAGE",
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
                        metadata={
                            "paragraphIndex": owning_paragraph_index,
                            "inlineShapeIndex": image_index,
                            "drawingIndex": drawing_index,
                        },
                    )
                )
                image_index += 1

        for child in docx.element.body.iterchildren():
            if isinstance(child, CT_P):
                current_paragraph_index = paragraph_index
                paragraph = Paragraph(child, docx)
                text = paragraph.text.strip()
                style = paragraph.style.name if paragraph.style else ""
                if text:
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
                            element_id=_eid(
                                source, context, f"paragraph:{current_paragraph_index}", kind
                            ),
                            type=kind,
                            order_index=len(elements),
                            text=text,
                            level=level,
                            section_path=tuple(section_path),
                            source_locator={
                                "paragraphIndex": current_paragraph_index,
                                "style": style,
                            },
                        )
                    )
                emit_inline_images(child, current_paragraph_index)
                paragraph_index += 1
            elif isinstance(child, CT_Tbl):
                table = Table(child, docx)
                if table_index >= context.limits.max_tables:
                    raise ParserError("PARSER_TABLE_LIMIT", "table limit exceeded")
                rows = [[cell.text for cell in row.cells] for row in table.rows]
                elements.append(
                    DocumentElement(
                        element_id=_eid(source, context, f"table:{table_index}", ElementType.TABLE),
                        type=ElementType.TABLE,
                        order_index=len(elements),
                        section_path=tuple(section_path),
                        source_locator={"tableIndex": table_index},
                        metadata={
                            "rows": rows,
                            "rowCount": len(rows),
                            "columnCount": max((len(row) for row in rows), default=0),
                        },
                    )
                )
                table_index += 1

        if image_index != len(inline_shapes):
            raise ParserError(
                "PARSER_DOCX_IMAGE_MAPPING", "not all DOCX inline shapes were mapped to body order"
            )
        return _doc(source, context, "docx-native-ordered", elements, candidates, [])
