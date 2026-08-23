from .base import (
    DocumentParserService,
    FileTypeHandlerRegistry,
    ParseContext,
    ParseLimits,
    ParserError,
    QualityProfile,
)
from .handlers import (
    DocxDocumentHandler,
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


def default_registry() -> FileTypeHandlerRegistry:
    return FileTypeHandlerRegistry([
        TextDocumentHandler(),
        MarkdownDocumentHandler(),
        DocxDocumentHandler(),
        PdfDocumentHandler(),
        ImageDocumentHandler("JPEG"),
        ImageDocumentHandler("PNG"),
        ImageDocumentHandler("TIFF"),
    ])


__all__ = [
    "DocumentParserService", "FileTypeHandlerRegistry", "ParseContext", "ParseLimits", "ParserError", "QualityProfile",
    "DocumentElement", "ElementType", "OcrCandidate", "ParsedDocument", "ParseQuality", "ParserIdentity", "QualityStatus", "SourceGeometry",
    "TextDocumentHandler", "MarkdownDocumentHandler", "DocxDocumentHandler", "PdfDocumentHandler", "ImageDocumentHandler", "default_registry",
]
