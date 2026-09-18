from __future__ import annotations

import math
from pathlib import Path

from services.document_schema import DOCUMENT_SCHEMA_NAME
from services.document_schema import DOCUMENT_SCHEMA_VERSION
from services.document_schema import default_block_derived
from services.document_schema import normalize_block_continuation_hint
from services.document_schema.providers import PROVIDER_GENERIC_FLAT_OCR


def looks_like_generic_flat_ocr(payload: dict) -> bool:
    return (
        isinstance(payload, dict)
        and str(payload.get("provider", "") or "") == PROVIDER_GENERIC_FLAT_OCR
        and isinstance(payload.get("pages"), list)
    )


_TEXT_LAYOUT_ROLE_MAP = {
    "title": "title",
    "heading": "heading",
    "abstract": "paragraph",
    "body": "paragraph",
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
    "footnote": "footnote",
}


def _block_kind(block: dict) -> str:
    return str(block.get("type", "text") or "text").strip().lower() or "text"


def _block_sub_type(block: dict) -> str:
    return str(block.get("sub_type", "body") or "body").strip().lower() or "body"


def _layout_role(block: dict) -> str:
    if _block_kind(block) != "text":
        return "unknown"
    return _TEXT_LAYOUT_ROLE_MAP.get(_block_sub_type(block), "unknown")


def _semantic_role(block: dict) -> str:
    if _block_kind(block) != "text":
        return "unknown"
    sub_type = _block_sub_type(block)
    if sub_type == "abstract":
        return "abstract"
    if sub_type in {"header", "footer", "page_number", "footnote", "metadata"}:
        return "metadata"
    if sub_type == "reference_entry":
        return "reference"
    if sub_type == "body":
        return "body"
    return "unknown"


def _structure_role(block: dict) -> str:
    if _block_kind(block) != "text":
        return ""
    return {
        "title": "title",
        "heading": "heading",
        "abstract": "body",
        "body": "body",
        "reference_entry": "reference_entry",
        "footnote": "footnote",
    }.get(_block_sub_type(block), "")


def _policy(block: dict) -> dict:
    kind = _block_kind(block)
    sub_type = _block_sub_type(block)
    if kind != "text":
        return {"translate": False, "translate_reason": f"provider_non_text:{kind}"}
    if sub_type == "abstract":
        return {"translate": True, "translate_reason": "provider_body_whitelist:abstract"}
    if sub_type == "body":
        return {"translate": True, "translate_reason": "provider_body_whitelist:body"}
    if sub_type == "heading":
        return {"translate": True, "translate_reason": "provider_body_whitelist:heading"}
    return {"translate": False, "translate_reason": f"provider_non_body:{sub_type or 'unknown'}"}


def _explicit_sub_type(block: dict) -> str:
    derived = dict(block.get("derived", {}) or default_block_derived())
    derived_role = str(derived.get("role", "") or "").strip().lower()
    return derived_role or _block_sub_type(block)


def _is_front_matter_author_gap(blocks: list[dict], index: int) -> bool:
    block = blocks[index]
    if _block_kind(block) != "text" or _block_sub_type(block) != "body":
        return False

    prev_explicit = ""
    for prev in reversed(blocks[:index]):
        prev_explicit = _explicit_sub_type(prev)
        if prev_explicit:
            break
    if prev_explicit != "title":
        return False

    next_explicit = ""
    for nxt in blocks[index + 1 :]:
        next_explicit = _explicit_sub_type(nxt)
        if next_explicit:
            break
    return next_explicit in {"abstract", "heading"}


def build_generic_flat_ocr_document(
    payload: dict,
    document_id: str,
    source_json_path: Path,
    provider_version: str,
) -> dict:
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ValueError("generic_flat_ocr payload must contain at least one page")
    pages = []
    for page_index, page in enumerate(raw_pages):
        if not isinstance(page, dict):
            raise ValueError(f"generic_flat_ocr page {page_index} is not an object")
        raw_page_index = page.get("page_index")
        if raw_page_index != page_index:
            raise ValueError(
                f"generic_flat_ocr page_index mismatch at page {page_index}: {raw_page_index}"
            )
        try:
            page_width = float(page["width"])
            page_height = float(page["height"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"generic_flat_ocr page {page_index} has invalid dimensions") from None
        if not math.isfinite(page_width) or not math.isfinite(page_height) or page_width <= 0 or page_height <= 0:
            raise ValueError(f"generic_flat_ocr page {page_index} dimensions must be finite and positive")
        if page.get("unit") != "pt":
            raise ValueError(f"generic_flat_ocr page {page_index} unit must be 'pt'")
        page_blocks = []
        raw_blocks_value = page.get("blocks")
        if not isinstance(raw_blocks_value, list) or not raw_blocks_value:
            raise ValueError(f"generic_flat_ocr page {page_index} must contain at least one positioned block")
        raw_blocks = list(raw_blocks_value)
        for order, block in enumerate(raw_blocks):
            if not isinstance(block, dict):
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} is not an object")
            if not isinstance(block.get("type"), str) or not str(block.get("type")).strip():
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} has invalid type")
            if not isinstance(block.get("sub_type"), str) or not str(block.get("sub_type")).strip():
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} has invalid sub_type")
            bbox_value = block.get("bbox")
            if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} has invalid bbox")
            try:
                bbox = [float(value) for value in bbox_value]
            except (TypeError, ValueError):
                raise ValueError(
                    f"generic_flat_ocr page {page_index} block {order} bbox is not numeric"
                ) from None
            if not all(math.isfinite(value) for value in bbox):
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} bbox is not finite")
            x0, y0, x1, y1 = bbox
            if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or x1 > page_width or y1 > page_height:
                raise ValueError(
                    f"generic_flat_ocr page {page_index} block {order} bbox is outside the page"
                )
            block_text_value = block.get("text")
            if not isinstance(block_text_value, str):
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} text is not a string")
            block_text = block_text_value
            if not block_text.strip():
                raise ValueError(f"generic_flat_ocr page {page_index} block {order} has empty text")
            block_lines = list(block.get("lines", []) or [])
            block_segments = list(block.get("segments", []) or [])
            # Generic OCR providers commonly return only text+bbox. The
            # translation extractor consumes normalized line spans, so create
            # one synthetic span when the provider did not emit line geometry.
            if not block_lines and not block_segments and block_text.strip():
                block_lines = [
                    {
                        "bbox": bbox if len(bbox) == 4 else [0, 0, 0, 0],
                        "spans": [
                            {
                                "type": "text",
                                "raw_type": "text",
                                "text": block_text,
                                "bbox": bbox if len(bbox) == 4 else [0, 0, 0, 0],
                            }
                        ],
                    }
                ]
            policy = _policy(block)
            semantic_role = _semantic_role(block)
            if _is_front_matter_author_gap(raw_blocks, order):
                semantic_role = "metadata"
                policy = {
                    "translate": False,
                    "translate_reason": "provider_front_matter:title_abstract_gap",
                }
            block_kind = _block_kind(block)
            page_blocks.append(
                {
                    "block_id": f"p{page_index + 1:03d}-b{order:04d}",
                    "page_index": page_index,
                    "order": order,
                    "type": str(block.get("type", "text") or "text"),
                    "sub_type": str(block.get("sub_type", "body") or "body"),
                    "bbox": bbox,
                    "geometry": {"bbox": bbox},
                    "content": {
                        "kind": block_kind,
                        "text": block_text,
                    },
                    "text": block_text,
                    "lines": block_lines,
                    "segments": block_segments,
                    "tags": list(block.get("tags", []) or []),
                    "derived": dict(block.get("derived", {}) or default_block_derived()),
                    "layout_role": _layout_role(block),
                    "semantic_role": semantic_role,
                    "structure_role": _structure_role(block),
                    "policy": policy,
                    "continuation_hint": normalize_block_continuation_hint(block.get("continuation_hint")),
                    "metadata": dict(block.get("metadata", {}) or {}),
                    "source": {
                        "provider": PROVIDER_GENERIC_FLAT_OCR,
                        "raw_page_index": page_index,
                        "raw_type": str(block.get("type", "text") or "text"),
                        "raw_sub_type": str(block.get("sub_type", "body") or "body"),
                        "raw_bbox": bbox,
                        "raw_text_excerpt": str(block.get("text", "") or "")[:200],
                    },
                }
            )
        pages.append(
            {
                "page_index": page_index,
                "width": page_width,
                "height": page_height,
                "unit": str(page.get("unit", "pt") or "pt"),
                "blocks": page_blocks,
            }
        )

    return {
        "schema": DOCUMENT_SCHEMA_NAME,
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": document_id,
        "doc_id": document_id,
        "source": {
            "provider": PROVIDER_GENERIC_FLAT_OCR,
            "provider_version": provider_version,
            "raw_files": {
                "source_json": str(source_json_path),
            },
        },
        "page_count": len(pages),
        "pages": pages,
        "assets": {},
        "derived": {
            "notes": "Adapted from generic_flat_ocr sample payload.",
        },
        "markers": {},
    }


__all__ = [
    "build_generic_flat_ocr_document",
    "looks_like_generic_flat_ocr",
]
