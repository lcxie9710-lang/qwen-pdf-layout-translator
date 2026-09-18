"""Qwen-OCR adapter for RetainPDF's generic_flat_ocr contract.

This adapter keeps the source PDF untouched. It renders one page at a time,
calls DashScope's built-in ``advanced_recognition`` OCR task, and converts the
returned positioned text lines into blocks that RetainPDF can consume.

The API key is read from DASHSCOPE_API_KEY or --api-key and is never written
to the output JSON.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import requests


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.5-ocr"
NATIVE_OCR_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


class RetryableOcrError(RuntimeError):
    """A transient transport or service error for which retry is safe."""


def _endpoint(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Qwen base URL must not be empty")
    value = base_url.strip().rstrip("/")
    if value.endswith(NATIVE_OCR_PATH):
        return value
    for suffix in ("/chat/completions", "/compatible-mode/v1", "/api/v1"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            return f"{value}{NATIVE_OCR_PATH}"
    raise ValueError(
        "Unsupported Qwen base URL. Expected a DashScope /compatible-mode/v1, /api/v1, "
        "or full multimodal-generation endpoint."
    )


def _call_qwen(
    *,
    image_data_url: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    if retries < 1:
        raise ValueError("retries must be at least 1")
    body = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": image_data_url,
                            "min_pixels": 3072,
                            "max_pixels": 8388608,
                            "enable_rotate": False,
                        }
                    ],
                }
            ]
        },
        "parameters": {"ocr_options": {"task": "advanced_recognition"}},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            try:
                response = requests.post(
                    _endpoint(base_url),
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            except requests.RequestException as error:
                raise RetryableOcrError(f"Qwen OCR transport error: {error}") from error
            if not response.ok:
                detail = response.text.strip().replace("\n", " ")[:800]
                message = f"Qwen OCR HTTP {response.status_code}: {detail}"
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableOcrError(message)
                raise RuntimeError(message)
            response_json = response.json()
            if not isinstance(response_json, dict):
                raise RuntimeError("Qwen OCR response root is not a JSON object")
            output = response_json.get("output")
            if not isinstance(output, dict):
                raise RuntimeError("Qwen OCR response has no output object")
            choices = output.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("Qwen OCR response has no non-empty output.choices array")
            if len(choices) != 1:
                raise RuntimeError(f"Qwen OCR response must contain exactly one choice; found {len(choices)}")
            if not isinstance(choices[0], dict):
                raise RuntimeError("Qwen OCR output.choices[0] is not an object")
            if choices[0].get("finish_reason") != "stop":
                raise RuntimeError(
                    f"Qwen OCR response did not finish cleanly: {choices[0].get('finish_reason')!r}"
                )
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise RuntimeError("Qwen OCR response has no message object")
            content = message.get("content")
            if not isinstance(content, list) or not content:
                raise RuntimeError("Qwen OCR response has no non-empty message.content array")
            found_results: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    raise RuntimeError("Qwen OCR message.content contains a non-object item")
                ocr_result = item.get("ocr_result")
                if ocr_result is not None:
                    if not isinstance(ocr_result, dict):
                        raise RuntimeError("Qwen OCR ocr_result is not an object")
                    found_results.append(ocr_result)
            if len(found_results) != 1:
                raise RuntimeError(
                    f"Qwen OCR response must contain exactly one ocr_result; found {len(found_results)}"
                )
            words_info = found_results[0].get("words_info")
            if not isinstance(words_info, list) or not words_info:
                raise RuntimeError("Qwen OCR ocr_result.words_info is missing or empty")
            return found_results[0]
        except RetryableOcrError as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(8, attempt * 2))
                continue
            break
    raise RuntimeError(f"Qwen OCR request failed after {retries} attempt(s): {last_error}")


def _render_page(page: fitz.Page, dpi: int) -> tuple[bytes, float, float]:
    scale = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pixmap.tobytes("png"), float(pixmap.width), float(pixmap.height)


def _ocr_page(
    page_data: tuple[int, bytes, float, float, float, float],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    retries: int,
) -> tuple[int, dict[str, Any]]:
    """OCR one already-rendered page in a worker thread."""
    page_index, image_bytes, image_width, image_height, page_width, page_height = page_data
    data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    try:
        response = _call_qwen(
            image_data_url=data_url,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            retries=retries,
        )
        blocks = _blocks_for_page(
            response,
            page_width=page_width,
            page_height=page_height,
            image_width=image_width,
            image_height=image_height,
        )
    except Exception as error:
        raise RuntimeError(f"Qwen OCR failed on page {page_index + 1}: {error}") from error
    return page_index, {
        "page_index": page_index,
        "width": page_width,
        "height": page_height,
        "unit": "pt",
        "blocks": blocks,
    }


def _validate_bbox(value: Any, image_width: float, image_height: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("OCR bbox must be a four-number array")
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        raise ValueError("OCR bbox contains a non-numeric value") from None
    if not all(math.isfinite(item) for item in (x0, y0, x1, y1)):
        raise ValueError("OCR bbox contains a non-finite value")
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"OCR bbox has invalid ordering: {[x0, y0, x1, y1]}")
    if x0 < 0 or y0 < 0 or x1 > image_width or y1 > image_height:
        raise ValueError(
            f"OCR bbox is outside the rendered image: {[x0, y0, x1, y1]} "
            f"vs {image_width}x{image_height}"
        )
    return [x0, y0, x1, y1]


def _bbox_from_location(value: Any, image_width: float, image_height: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 8:
        raise ValueError("Qwen OCR location must contain eight coordinates")
    try:
        points = [float(item) for item in value]
    except (TypeError, ValueError):
        raise ValueError("Qwen OCR location contains a non-numeric value") from None
    if not all(math.isfinite(item) for item in points):
        raise ValueError("Qwen OCR location contains a non-finite value")
    xs = points[0::2]
    ys = points[1::2]
    return _validate_bbox([min(xs), min(ys), max(xs), max(ys)], image_width, image_height)


def _bbox_from_rotate_rect(value: Any, image_width: float, image_height: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 5:
        raise ValueError("Qwen OCR rotate_rect must contain five values")
    try:
        center_x, center_y, width, height, angle = (float(item) for item in value)
    except (TypeError, ValueError):
        raise ValueError("Qwen OCR rotate_rect contains a non-numeric value") from None
    if not all(math.isfinite(item) for item in (center_x, center_y, width, height, angle)):
        raise ValueError("Qwen OCR rotate_rect contains a non-finite value")
    if width <= 0 or height <= 0:
        raise ValueError("Qwen OCR rotate_rect width and height must be positive")
    if angle < -90 or angle > 90:
        raise ValueError(f"Qwen OCR rotate_rect angle is outside [-90, 90]: {angle}")
    radians = math.radians(angle)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    corners: list[tuple[float, float]] = []
    for local_x, local_y in (
        (-width / 2, -height / 2),
        (width / 2, -height / 2),
        (width / 2, height / 2),
        (-width / 2, height / 2),
    ):
        corners.append(
            (
                center_x + local_x * cos_a - local_y * sin_a,
                center_y + local_x * sin_a + local_y * cos_a,
            )
        )
    return _validate_bbox(
        [
            min(point[0] for point in corners),
            min(point[1] for point in corners),
            max(point[0] for point in corners),
            max(point[1] for point in corners),
        ],
        image_width,
        image_height,
    )


def _vertical_overlap_ratio(left: list[float], right: list[float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    shorter = min(left[3] - left[1], right[3] - right[1])
    return overlap / shorter if shorter > 0 else 0.0


def _vertical_gap(left: list[float], right: list[float]) -> float:
    if left[3] < right[1]:
        return right[1] - left[3]
    if right[3] < left[1]:
        return left[1] - right[3]
    return 0.0


def _horizontal_gap(left: list[float], right: list[float]) -> float:
    if left[2] < right[0]:
        return right[0] - left[2]
    if right[2] < left[0]:
        return left[0] - right[2]
    return 0.0


def _merge_vertical_text_columns(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Qwen's line-level vertical Japanese columns into layout regions.

    RetainPDF lays translated text out horizontally. Feeding one narrow vertical
    source column per block therefore creates overlapping Chinese text.  This
    routine performs geometry-only grouping: adjacent thin vertical boxes in
    the same y band become one paragraph-sized block.  Isolated horizontal
    labels and unrelated page regions remain untouched.
    """

    vertical_indexes = []
    for index, block in enumerate(blocks):
        x0, y0, x1, y1 = block["bbox"]
        width = x1 - x0
        height = y1 - y0
        if height >= 20.0 and height >= width * 2.0:
            vertical_indexes.append(index)

    if not vertical_indexes:
        return blocks

    remaining = set(vertical_indexes)
    components: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            current_bbox = blocks[current]["bbox"]
            current_width = current_bbox[2] - current_bbox[0]
            for candidate in list(remaining):
                candidate_bbox = blocks[candidate]["bbox"]
                candidate_width = candidate_bbox[2] - candidate_bbox[0]
                close_x = _horizontal_gap(current_bbox, candidate_bbox) <= max(
                    18.0, 1.5 * max(current_width, candidate_width)
                )
                same_band = (
                    _vertical_overlap_ratio(current_bbox, candidate_bbox) >= 0.45
                    or _vertical_gap(current_bbox, candidate_bbox) <= 12.0
                )
                if close_x and same_band:
                    remaining.remove(candidate)
                    component.append(candidate)
                    frontier.append(candidate)
        components.append(component)

    merged_by_first_index: dict[int, dict[str, Any]] = {}
    consumed: set[int] = set()
    for component in components:
        if len(component) < 2:
            continue
        members = [blocks[index] for index in component]
        ordered = sorted(
            members,
            key=lambda block: (
                -(block["bbox"][0] + block["bbox"][2]) / 2.0,
                block["bbox"][1],
            ),
        )
        bbox = [
            min(block["bbox"][0] for block in members),
            min(block["bbox"][1] for block in members),
            max(block["bbox"][2] for block in members),
            max(block["bbox"][3] for block in members),
        ]
        first_index = min(component)
        source_orders = [int(block["metadata"]["ocr_order"]) for block in ordered]
        merged_by_first_index[first_index] = {
            "type": "text",
            "sub_type": "body",
            "text": "".join(block["text"] for block in ordered),
            "bbox": bbox,
            "lines": [],
            "segments": [],
            "metadata": {
                "ocr_provider": "qwen_ocr_advanced_recognition",
                "coordinate_source": "merged_official_locations",
                "ocr_order": min(source_orders),
                "source_ocr_orders": source_orders,
                "vertical_columns_merged": len(members),
            },
        }
        consumed.update(component)

    result: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if index in merged_by_first_index:
            result.append(merged_by_first_index[index])
        elif index not in consumed:
            result.append(block)
    return result


def _blocks_for_page(
    payload: dict[str, Any],
    *,
    page_width: float,
    page_height: float,
    image_width: float,
    image_height: float,
) -> list[dict[str, Any]]:
    if not all(
        math.isfinite(value) and value > 0
        for value in (page_width, page_height, image_width, image_height)
    ):
        raise ValueError("Page and rendered-image dimensions must be finite and positive")
    blocks: list[dict[str, Any]] = []
    words_info = payload.get("words_info")
    if not isinstance(words_info, list) or not words_info:
        raise RuntimeError("Qwen OCR words_info must be a non-empty array")
    for order, raw in enumerate(words_info):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Qwen OCR words_info[{order}] is not an object")
        raw_text = raw.get("text")
        if not isinstance(raw_text, str):
            raise RuntimeError(f"Qwen OCR words_info[{order}].text is not a string")
        text = raw_text.strip()
        if not text:
            raise RuntimeError(f"Qwen OCR words_info[{order}].text is empty")
        has_location = "location" in raw and raw.get("location") is not None
        has_rotate_rect = "rotate_rect" in raw and raw.get("rotate_rect") is not None
        if has_location:
            bbox = _bbox_from_location(raw["location"], image_width, image_height)
            coordinate_source = "location"
        elif has_rotate_rect:
            bbox = _bbox_from_rotate_rect(raw.get("rotate_rect"), image_width, image_height)
            coordinate_source = "rotate_rect"
        else:
            raise RuntimeError(
                f"Qwen OCR words_info[{order}] has text but neither location nor rotate_rect"
            )
        x0, y0, x1, y1 = bbox
        coverage_ratio = ((x1 - x0) * (y1 - y0)) / (image_width * image_height)
        if coverage_ratio >= 0.65:
            raise RuntimeError(
                f"Qwen OCR returned a suspicious text box covering {coverage_ratio:.1%} of the page; "
                "refusing to erase the source image"
            )
        pdf_bbox = [
            x0 * page_width / image_width,
            y0 * page_height / image_height,
            x1 * page_width / image_width,
            y1 * page_height / image_height,
        ]
        blocks.append(
            {
                "type": "text",
                "sub_type": "body",
                "text": text,
                "bbox": pdf_bbox,
                "lines": [],
                "segments": [],
                "metadata": {
                    "ocr_provider": "qwen_ocr_advanced_recognition",
                    "coordinate_source": coordinate_source,
                    "ocr_order": order,
                    "page_coverage_ratio": coverage_ratio,
                    "location": list(raw.get("location") or []),
                    "rotate_rect": list(raw.get("rotate_rect") or []),
                },
            }
        )
    if len(blocks) != len(words_info):
        raise RuntimeError(
            f"Qwen OCR positioned block count mismatch: {len(blocks)} != {len(words_info)}"
        )
    return _merge_vertical_text_columns(blocks)


def convert_pdf(
    input_path: Path,
    output_path: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    dpi: int,
    start_page: int,
    end_page: int,
    timeout: int,
    retries: int,
    workers: int,
) -> None:
    if not api_key.strip():
        raise ValueError("Missing Qwen API key. Set DASHSCOPE_API_KEY or use --api-key.")
    if not model.strip():
        raise ValueError("Qwen OCR model must not be empty")
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if dpi < 72:
        raise ValueError("DPI must be at least 72")
    if start_page < 0:
        raise ValueError("start_page must be zero or greater")
    if end_page != -1 and end_page <= start_page:
        raise ValueError("end_page must be greater than start_page, or -1 for all pages")
    if timeout < 1:
        raise ValueError("timeout must be positive")
    if retries < 1:
        raise ValueError("retries must be at least 1")
    if workers < 1:
        raise ValueError("workers must be at least 1")

    pages_by_index: dict[int, dict[str, Any]] = {}
    with fitz.open(input_path) as document:
        if len(document) == 0:
            raise RuntimeError("Input PDF has no pages")
        if start_page >= len(document):
            raise ValueError(f"start_page {start_page} is outside the {len(document)}-page PDF")
        if end_page > len(document):
            raise ValueError(f"end_page {end_page} is outside the {len(document)}-page PDF")
        first = start_page
        last = end_page if end_page >= 0 else len(document)
        worker_count = workers
        print(
            f"Qwen OCR: sending up to {worker_count} pages in parallel "
            f"({last - first} page(s))",
            flush=True,
        )
        # Keep only one worker-sized batch of rendered images in memory. HTTP
        # requests within each batch run concurrently; output is sorted below.
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for batch_start in range(first, last, worker_count):
                batch_end = min(batch_start + worker_count, last)
                prepared: list[tuple[int, bytes, float, float, float, float]] = []
                for page_index in range(batch_start, batch_end):
                    page = document[page_index]
                    image_bytes, image_width, image_height = _render_page(page, dpi)
                    prepared.append(
                        (
                            page_index,
                            image_bytes,
                            image_width,
                            image_height,
                            float(page.rect.width),
                            float(page.rect.height),
                        )
                    )
                    print(f"Qwen OCR: page {page_index + 1}/{len(document)} submitted", flush=True)

                futures = {
                    executor.submit(
                        _ocr_page,
                        page_data,
                        api_key=api_key.strip(),
                        base_url=base_url,
                        model=model,
                        timeout=timeout,
                        retries=retries,
                    ): page_data
                    for page_data in prepared
                }
                failed_pages: list[tuple[tuple[int, bytes, float, float, float, float], Exception]] = []
                for future in as_completed(futures):
                    page_data = futures[future]
                    try:
                        page_index, page_record = future.result()
                        pages_by_index[page_index] = page_record
                        print(
                            f"Qwen OCR: page {page_index + 1}/{len(document)} done "
                            f"({len(page_record['blocks'])} positioned line(s))",
                            flush=True,
                        )
                    except Exception as error:
                        failed_pages.append((page_data, error))
                        print(
                            f"Qwen OCR: page {page_data[0] + 1}/{len(document)} parallel request failed; "
                            "will retry sequentially",
                            flush=True,
                        )

                # Concurrent TLS connections can occasionally be closed by the
                # upstream edge. Keep successful pages and retry only failures
                # one by one with a fresh request sequence.
                for page_data, parallel_error in sorted(failed_pages, key=lambda item: item[0][0]):
                    page_index = page_data[0]
                    print(
                        f"Qwen OCR: page {page_index + 1}/{len(document)} sequential fallback started",
                        flush=True,
                    )
                    time.sleep(1)
                    try:
                        page_index, page_record = _ocr_page(
                            page_data,
                            api_key=api_key.strip(),
                            base_url=base_url,
                            model=model,
                            timeout=timeout,
                            retries=max(retries, 5),
                        )
                    except Exception as fallback_error:
                        raise RuntimeError(
                            f"Qwen OCR page {page_index + 1} failed in parallel and sequential fallback. "
                            f"Parallel error: {parallel_error}; fallback error: {fallback_error}"
                        ) from fallback_error
                    pages_by_index[page_index] = page_record
                    print(
                        f"Qwen OCR: page {page_index + 1}/{len(document)} recovered "
                        f"({len(page_record['blocks'])} positioned line(s))",
                        flush=True,
                    )

    expected_page_count = last - first
    if len(pages_by_index) != expected_page_count:
        raise RuntimeError(
            f"OCR page count mismatch: received {len(pages_by_index)}, expected {expected_page_count}"
        )
    pages = [pages_by_index[index] for index in sorted(pages_by_index)]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "provider": "generic_flat_ocr",
                "provider_version": model,
                "pages": pages,
                "_meta": {
                    "source": "qwen_ocr",
                    "model": model,
                    "task": "advanced_recognition",
                    "dpi": dpi,
                    "unsafe_full_page_fallback": False,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"OCR JSON: {output_path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Qwen OCR and emit RetainPDF generic_flat_ocr JSON")
    parser.add_argument("--input", required=True, help="Input PDF")
    parser.add_argument("--output", required=True, help="Output generic OCR JSON")
    parser.add_argument("--api-key", default=os.environ.get("DASHSCOPE_API_KEY", ""))
    parser.add_argument("--base-url", default=os.environ.get("QWEN_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.environ.get("QWEN_OCR_MODEL", DEFAULT_MODEL))
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--start-page", type=int, default=0, help="Zero-based inclusive page")
    parser.add_argument("--end-page", type=int, default=-1, help="Zero-based exclusive page; -1 means all")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("QWEN_OCR_WORKERS", "3")),
        help="Concurrent OCR requests; start with 3 and lower if rate limited",
    )
    args = parser.parse_args()
    convert_pdf(
        Path(args.input).resolve(),
        Path(args.output).resolve(),
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        dpi=args.dpi,
        start_page=args.start_page,
        end_page=args.end_page,
        timeout=args.timeout,
        retries=args.retries,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
