from .base import (
    DocumentParserService,
    FileTypeHandlerRegistry,
    ParseContext,
    ParseLimits,
    ParserError,
    QualityProfile,
)
from .extended_fixes import EpubDocumentHandlerV1, PptxDocumentHandlerV1
from .extended_handlers import (
    CsvDocumentHandler,
    HtmlDocumentHandler,
    OdtDocumentHandler,
    RtfDocumentHandler,
    XlsxDocumentHandler,
)
from .handlers import (
    ImageDocumentHandler,
    MarkdownDocumentHandler,
    PdfDocumentHandler,
    TextDocumentHandler,
)
from .model import (
    DocumentElement,
    ElementType,
    OcrCandidate,
    ParsedDocument,
    ParseQuality,
    ParserIdentity,
    QualityStatus,
    SourceGeometry,
)
from .ordered_docx import OrderedDocxDocumentHandler

DocxDocumentHandler = OrderedDocxDocumentHandler
PptxDocumentHandler = PptxDocumentHandlerV1
EpubDocumentHandler = EpubDocumentHandlerV1


def default_registry() -> FileTypeHandlerRegistry:
    return FileTypeHandlerRegistry(
        [
            TextDocumentHandler(),
            MarkdownDocumentHandler(),
            DocxDocumentHandler(),
            PdfDocumentHandler(),
            ImageDocumentHandler("JPEG"),
            ImageDocumentHandler("PNG"),
            ImageDocumentHandler("TIFF"),
            XlsxDocumentHandler(),
            PptxDocumentHandler(),
            CsvDocumentHandler(),
            HtmlDocumentHandler(),
            OdtDocumentHandler(),
            RtfDocumentHandler(),
            EpubDocumentHandler(),
        ]
    )


__all__ = [
    "DocumentParserService",
    "FileTypeHandlerRegistry",
    "ParseContext",
    "ParseLimits",
    "ParserError",
    "QualityProfile",
    "DocumentElement",
    "ElementType",
    "OcrCandidate",
    "ParsedDocument",
    "ParseQuality",
    "ParserIdentity",
    "QualityStatus",
    "SourceGeometry",
    "TextDocumentHandler",
    "MarkdownDocumentHandler",
    "DocxDocumentHandler",
    "PdfDocumentHandler",
    "ImageDocumentHandler",
    "XlsxDocumentHandler",
    "PptxDocumentHandler",
    "CsvDocumentHandler",
    "HtmlDocumentHandler",
    "OdtDocumentHandler",
    "RtfDocumentHandler",
    "EpubDocumentHandler",
    "default_registry",
]
