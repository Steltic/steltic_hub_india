#!/usr/bin/env python3
"""Highest-accuracy Docling PDF conversion (Python API).

Converts a PDF with TableFormer ACCURATE, formula enrichment ON, and OCR
fallback (RapidOCR). Designed for CPU-only boxes with limited RAM: page-range
chunking is supported so a crash can resume without lowering accuracy flags.

Usage:
  TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata/ \
    python <corpus>/scripts/convert_pdf.py \
    /path/to/your_licensed.pdf <corpus>/documents/standards/<STEM>

  Optional:
    --chunk-pages 5     Convert in 5-page windows (OOM workaround).
    --page-range 1-180  Limit to a PDF page range (1-based, inclusive).
    --stem NAME         Output basename (default: sanitized PDF stem).
    --symlink-original  Create output_dir/original/<pdf-name> -> source PDF.
    --no-postprocess    Skip the body-only / commentary / eq-id post-pass.
    --commentary-profile auto|aisi_s400|aisi_s100|aisi_s230|aisi_s240|asce7|asce_41|aisc|aisc_358|aisc_341|aisc_342

Markdown: searchable output is BODY-only (no running titles, no AISI copyright
footer). Furniture/headers are written to markdown/<stem>.furniture.md and
structured/furniture_by_page.json so postprocess can detect commentary
boundaries and printed page labels. Accuracy flags are never lowered.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# tesserocr (fallback) requires TESSDATA_PREFIX ending with /
os.environ.setdefault("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata/")
if not os.environ["TESSDATA_PREFIX"].endswith("/"):
    os.environ["TESSDATA_PREFIX"] += "/"

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.backend_options import ThreadedDoclingParseBackendOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import (
    HeadingHierarchyOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.datamodel.settings import settings
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel, TableItem, TextItem
from docling_core.types.doc.document import ContentLayer, DEFAULT_CONTENT_LAYERS, DoclingDocument

# Retrieval default: body only. Furniture (headers/footers) is a side channel
# used later for commentary detection and printed-page labels.
BODY_LAYERS = set(DEFAULT_CONTENT_LAYERS) or {ContentLayer.BODY}
ALL_LAYERS = set(ContentLayer)

LOG = logging.getLogger("convert_pdf")

DOCLING_VERSION = "2.123.1"

# Conservative pipeline batching for ~15 GB RAM / no GPU.
PAGE_BATCH_SIZE = 1
OCR_BATCH_SIZE = 1
LAYOUT_BATCH_SIZE = 1
TABLE_BATCH_SIZE = 1
ELEMENTS_BATCH_SIZE = 4
QUEUE_MAX_SIZE = 4
RELEASE_NATIVE_EVERY_N = 4
NUM_THREADS = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_stem(name: str) -> str:
    stem = Path(name).stem
    stem = stem.replace("-", "_")
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", stem)
    return stem.strip("_") or "document"


def parse_page_range(text: Optional[str], n_pages: int) -> tuple[int, int]:
    if not text:
        return 1, n_pages
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", text)
    if not m:
        raise ValueError(f"Invalid --page-range {text!r}; expected START-END")
    start, end = int(m.group(1)), int(m.group(2))
    if start < 1 or end < start:
        raise ValueError(f"Invalid --page-range {text!r}")
    return start, min(end, n_pages)


def pdf_page_count(pdf_path: Path) -> int:
    """Page count via pypdfium2 (does not modify the PDF)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def build_pipeline_options(
    *,
    generate_page_images: bool = False,
    images_scale: float = 1.0,
) -> PdfPipelineOptions:
    """Highest-accuracy CPU pipeline for this Docling 2.123.1 install."""
    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.do_formula_enrichment = True
    opts.do_code_enrichment = False
    opts.do_picture_classification = False
    opts.do_picture_description = False
    # Default off (RAM). Page-range recovery may enable this; accuracy flags stay.
    opts.generate_page_images = bool(generate_page_images)
    opts.generate_picture_images = False
    opts.generate_parsed_pages = False
    opts.force_backend_text = False
    opts.document_timeout = None
    opts.images_scale = float(images_scale)

    opts.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )
    # RapidOCR is the preferred CPU OCR engine. Explicit English (default is chinese).
    opts.ocr_options = RapidOcrOptions(lang=["english"])

    opts.heading_hierarchy_options = HeadingHierarchyOptions(
        enabled=True,
        use_bookmarks=True,
        use_numbering=True,
        use_style=False,  # style needs generate_parsed_pages; skip to save RAM
    )

    opts.accelerator_options = AcceleratorOptions(
        num_threads=NUM_THREADS,
        device=AcceleratorDevice.CPU,
    )
    opts.ocr_batch_size = OCR_BATCH_SIZE
    opts.layout_batch_size = LAYOUT_BATCH_SIZE
    opts.table_batch_size = TABLE_BATCH_SIZE
    opts.queue_max_size = QUEUE_MAX_SIZE
    return opts


def flags_record() -> dict[str, Any]:
    return {
        "table_mode": "accurate",
        "table_structure_engine": "docling_tableformer",
        "table_do_cell_matching": True,
        "do_formula_enrichment": True,
        "code_formula_preset": "codeformulav2",
        "do_ocr": True,
        "ocr_engine": "rapidocr",
        "ocr_lang": ["english"],
        "ocr_mode": "default (pdf_aware_layout_regions)",
        "layout_engine": "layout_object_detection",
        "layout_preset": "layout_heron_default",
        "pdf_backend": "threaded_docling_parse",
        "device": "cpu",
        "num_threads": NUM_THREADS,
        "do_code_enrichment": False,
        "heading_hierarchy": True,
        "page_batch_size": PAGE_BATCH_SIZE,
        "ocr_batch_size": OCR_BATCH_SIZE,
        "layout_batch_size": LAYOUT_BATCH_SIZE,
        "table_batch_size": TABLE_BATCH_SIZE,
        "elements_batch_size": ELEMENTS_BATCH_SIZE,
        "queue_max_size": QUEUE_MAX_SIZE,
        "release_native_memory_every_n_pages": RELEASE_NATIVE_EVERY_N,
        "generate_page_images": False,  # overwritten in main() when CLI flag set
        "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX"),
    }


def make_converter(
    *,
    generate_page_images: bool = False,
    images_scale: float = 1.0,
) -> DocumentConverter:
    settings.perf.page_batch_size = PAGE_BATCH_SIZE
    settings.perf.elements_batch_size = ELEMENTS_BATCH_SIZE
    settings.perf.page_batch_concurrency = 1
    settings.perf.doc_batch_size = 1
    settings.perf.doc_batch_concurrency = 1

    pipeline = build_pipeline_options(
        generate_page_images=generate_page_images,
        images_scale=images_scale,
    )
    backend_opts = ThreadedDoclingParseBackendOptions(
        release_native_memory_every_n_pages=RELEASE_NATIVE_EVERY_N,
        parser_threads=NUM_THREADS,
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline,
                backend_options=backend_opts,
            )
        },
    )


def page_nos(item: Any) -> list[int]:
    prov = getattr(item, "prov", None) or []
    out: list[int] = []
    for p in prov:
        n = getattr(p, "page_no", None)
        if n is not None:
            out.append(int(n))
    return out


def export_tables(doc: DoclingDocument, tables_dir: Path, stem: str) -> list[dict[str, Any]]:
    tables_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict[str, Any]] = []
    idx = 0
    for item, _level in doc.iterate_items(included_content_layers=set(ContentLayer)):
        if not isinstance(item, TableItem):
            continue
        idx += 1
        pages = page_nos(item)
        caption = ""
        try:
            captions = getattr(item, "captions", None) or []
            bits = []
            for cap_ref in captions:
                try:
                    cap = cap_ref.resolve(doc)
                    bits.append(getattr(cap, "text", "") or "")
                except Exception:
                    pass
            caption = " ".join(b for b in bits if b).strip()
        except Exception:
            caption = ""
        page_tag = f"p{pages[0]:03d}" if pages else "pxxx"
        safe_cap = re.sub(r"[^A-Za-z0-9._-]+", "_", caption)[:60].strip("_")
        base = f"{stem}_{idx:03d}_{page_tag}"
        if safe_cap:
            base = f"{base}_{safe_cap}"
        md_path = tables_dir / f"{base}.md"
        csv_path = tables_dir / f"{base}.csv"
        json_path = tables_dir / f"{base}.json"
        try:
            md = item.export_to_markdown(doc=doc)
        except Exception:
            md = ""
        try:
            df = item.export_to_dataframe(doc=doc)
            df.to_csv(csv_path, index=False)
        except Exception:
            csv_path = None
        payload = {
            "index": idx,
            "self_ref": getattr(item, "self_ref", None),
            "pages": pages,
            "caption": caption,
            "num_rows": getattr(item.data, "num_rows", None),
            "num_cols": getattr(item.data, "num_cols", None),
            "markdown": md,
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(md or "", encoding="utf-8")
        meta.append(
            {
                "index": idx,
                "pages": pages,
                "caption": caption,
                "num_rows": payload["num_rows"],
                "num_cols": payload["num_cols"],
                "md": str(md_path),
                "csv": str(csv_path) if csv_path else None,
                "json": str(json_path),
            }
        )
    return meta


def export_equations(doc: DoclingDocument, eq_dir: Path, stem: str) -> list[dict[str, Any]]:
    eq_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict[str, Any]] = []
    idx = 0
    lines: list[str] = []
    for item, _level in doc.iterate_items(included_content_layers=set(ContentLayer)):
        if not isinstance(item, TextItem):
            continue
        if item.label != DocItemLabel.FORMULA:
            continue
        idx += 1
        pages = page_nos(item)
        text = item.text or ""
        orig = getattr(item, "orig", None) or ""
        rec = {
            "index": idx,
            "self_ref": getattr(item, "self_ref", None),
            "pages": pages,
            "text": text,
            "orig": orig,
        }
        meta.append(rec)
        page_tag = f"p{pages[0]:03d}" if pages else "pxxx"
        (eq_dir / f"{stem}_{idx:03d}_{page_tag}.txt").write_text(
            f"pages={pages}\ntext={text}\norig={orig}\n", encoding="utf-8"
        )
        lines.append(f"## eq {idx} pages={pages}\n\n$$\n{text}\n$$\n")
    (eq_dir / f"{stem}_equations.md").write_text("\n".join(lines), encoding="utf-8")
    (eq_dir / f"{stem}_equations.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def export_page_markdown(doc: DoclingDocument, md_dir: Path, pages: Iterable[int]) -> None:
    """Write per-page BODY-only markdown for retrieval."""
    pages_dir = md_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        try:
            text = doc.export_to_markdown(
                page_no=p,
                page_break_placeholder=None,
                included_content_layers=BODY_LAYERS,
            )
        except Exception:
            text = ""
        (pages_dir / f"page_{p:03d}.md").write_text(text or "", encoding="utf-8")


def export_furniture_pages(doc: DoclingDocument, md_dir: Path, pages: Iterable[int]) -> None:
    """Side channel: all-layer per-page markdown (headers needed for detection)."""
    furn_dir = md_dir / "furniture" / "pages"
    furn_dir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        try:
            text = doc.export_to_markdown(
                page_no=p,
                page_break_placeholder=None,
                included_content_layers=ALL_LAYERS,
            )
        except Exception:
            text = ""
        (furn_dir / f"page_{p:03d}.md").write_text(text or "", encoding="utf-8")


def extract_furniture_records(doc: DoclingDocument) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for item, _level in doc.iterate_items(included_content_layers=ALL_LAYERS):
        label = getattr(item, "label", None)
        layer = getattr(item, "content_layer", None)
        lab = getattr(label, "value", str(label) if label is not None else "")
        lay = getattr(layer, "value", str(layer) if layer is not None else "")
        if lab not in ("page_header", "page_footer") and lay != "furniture":
            continue
        recs.append(
            {
                "self_ref": getattr(item, "self_ref", None),
                "label": lab,
                "content_layer": lay,
                "text": getattr(item, "text", None) or "",
                "pages": page_nos(item),
            }
        )
    return recs


def model_names_from_cache() -> list[str]:
    cache = Path.home() / ".cache" / "docling" / "models"
    if not cache.exists():
        return []
    names = []
    for child in sorted(cache.iterdir()):
        if child.is_dir():
            names.append(child.name)
    return names


def convert_chunk(
    converter: DocumentConverter,
    pdf_path: Path,
    start: int,
    end: int,
) -> tuple[Any, DoclingDocument]:
    LOG.info("Converting pages %s-%s of %s", start, end, pdf_path)
    result = converter.convert(
        source=str(pdf_path),
        raises_on_error=False,
        page_range=(start, end),
    )
    status = getattr(result, "status", None)
    if status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = getattr(result, "errors", None)
        raise RuntimeError(
            f"Conversion failed for pages {start}-{end}: status={status} errors={errors}"
        )
    return result, result.document


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Highest-accuracy Docling PDF conversion")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--page-range", default=None, help="START-END (1-based, inclusive)")
    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=0,
        help="If >0, convert in windows of N pages (OOM workaround). 0 = whole range.",
    )
    parser.add_argument("--stem", default=None)
    parser.add_argument(
        "--symlink-original",
        action="store_true",
        help="Create output_dir/original/<pdf-name> symlink to the source PDF",
    )
    parser.add_argument(
        "--no-postprocess",
        action="store_true",
        help="Skip postprocess.py (body-only search md, commentary, eq ids, printed labels)",
    )
    parser.add_argument(
        "--commentary-profile",
        default="auto",
        help="Passed to postprocess.py: auto|aisi_s400|aisi_s100|aisi_s230|aisi_s240|asce7|asce_41|aisc|aisc_358|aisc_341|aisc_342",
    )
    parser.add_argument(
        "--generate-page-images",
        action="store_true",
        help="Enable Docling generate_page_images (page-range table recovery). Accuracy flags unchanged.",
    )
    parser.add_argument(
        "--images-scale",
        type=float,
        default=1.0,
        help="Page-image scale (1.0=72 dpi). Use ~4.17 for 300 dpi recovery windows.",
    )
    args = parser.parse_args(argv)

    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        LOG.error("PDF not found: %s", pdf_path)
        return 2

    out_dir = args.output_dir.resolve()
    md_dir = out_dir / "markdown"
    structured_dir = out_dir / "structured"
    tables_dir = out_dir / "tables"
    eq_dir = out_dir / "equations"
    chunks_md = md_dir / "chunks"
    chunks_json = structured_dir / "chunks"
    furn_chunks_md = md_dir / "furniture" / "chunks"
    for d in (md_dir, structured_dir, tables_dir, eq_dir, chunks_md, chunks_json, furn_chunks_md):
        d.mkdir(parents=True, exist_ok=True)

    stem = args.stem or sanitize_stem(pdf_path.name)

    if args.symlink_original:
        orig_dir = out_dir / "original"
        orig_dir.mkdir(parents=True, exist_ok=True)
        link = orig_dir / pdf_path.name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(pdf_path)

    n_pages = pdf_page_count(pdf_path)
    range_start, range_end = parse_page_range(args.page_range, n_pages)
    chunk_n = args.chunk_pages if args.chunk_pages and args.chunk_pages > 0 else (
        range_end - range_start + 1
    )

    windows: list[tuple[int, int]] = []
    s = range_start
    while s <= range_end:
        e = min(s + chunk_n - 1, range_end)
        windows.append((s, e))
        s = e + 1

    meta_path = out_dir / "convert_meta.json"
    t0 = time.perf_counter()
    started = _now_iso()
    chunk_records: list[dict[str, Any]] = []
    converter: Optional[DocumentConverter] = None
    last_doc: Optional[DoclingDocument] = None
    all_table_meta: list[dict[str, Any]] = []
    all_eq_meta: list[dict[str, Any]] = []
    md_parts: list[str] = []
    furniture_parts: list[str] = []
    furniture_records: list[dict[str, Any]] = []
    errors: list[str] = []
    status = "success"

    LOG.info(
        "PDF=%s pages=%s converting %s-%s in %s window(s) of <=%s pages",
        pdf_path,
        n_pages,
        range_start,
        range_end,
        len(windows),
        chunk_n,
    )

    try:
        converter = make_converter(
            generate_page_images=bool(args.generate_page_images),
            images_scale=float(args.images_scale),
        )
        for (start, end) in windows:
            chunk_stem = f"{stem}_p{start:03d}_{end:03d}"
            json_chunk = chunks_json / f"{chunk_stem}.json"
            md_chunk = chunks_md / f"{chunk_stem}.md"
            t_chunk = time.perf_counter()
            try:
                if json_chunk.is_file() and json_chunk.stat().st_size > 100 and md_chunk.is_file():
                    LOG.info("Resume: reusing existing chunk pages %s-%s", start, end)
                    try:
                        doc = DoclingDocument.load_from_json(json_chunk)
                        last_doc = doc
                        export_page_markdown(doc, md_dir, range(start, end + 1))
                        export_furniture_pages(doc, md_dir, range(start, end + 1))
                        furniture_records.extend(extract_furniture_records(doc))
                        md_body = doc.export_to_markdown(
                            page_break_placeholder="\n\n<!-- page break -->\n\n",
                            included_content_layers=BODY_LAYERS,
                        )
                        md_all = doc.export_to_markdown(
                            page_break_placeholder="\n\n<!-- page break -->\n\n",
                            included_content_layers=ALL_LAYERS,
                        )
                        md_chunk.write_text(md_body, encoding="utf-8")
                        (furn_chunks_md / f"{chunk_stem}.md").write_text(md_all, encoding="utf-8")
                        furniture_parts.append(f"<!-- pdf-pages {start}-{end} -->\n\n{md_all}")
                        md = md_body
                        md_parts.append(f"<!-- pdf-pages {start}-{end} -->\n\n{md_body}")
                        all_table_meta.extend(
                            export_tables(doc, tables_dir, f"{stem}_p{start:03d}_{end:03d}")
                        )
                        all_eq_meta.extend(
                            export_equations(doc, eq_dir, f"{stem}_p{start:03d}_{end:03d}")
                        )
                    except Exception:
                        LOG.exception("Resume reload failed for %s-%s; reconverting", start, end)
                    else:
                        chunk_records.append(
                            {
                                "start": start,
                                "end": end,
                                "status": "resumed",
                                "wall_s": round(time.perf_counter() - t_chunk, 3),
                                "markdown": str(md_chunk),
                                "json": str(json_chunk),
                                "json_bytes": file_size(json_chunk),
                                "md_bytes": file_size(md_chunk),
                            }
                        )
                        continue
                result, doc = convert_chunk(converter, pdf_path, start, end)
                last_doc = doc
                md = doc.export_to_markdown(
                    page_break_placeholder="\n\n<!-- page break -->\n\n",
                    included_content_layers=BODY_LAYERS,
                )
                md_all = doc.export_to_markdown(
                    page_break_placeholder="\n\n<!-- page break -->\n\n",
                    included_content_layers=ALL_LAYERS,
                )
                md_chunk.write_text(md, encoding="utf-8")
                (furn_chunks_md / f"{chunk_stem}.md").write_text(md_all, encoding="utf-8")
                md_parts.append(f"<!-- pdf-pages {start}-{end} -->\n\n{md}")
                furniture_parts.append(f"<!-- pdf-pages {start}-{end} -->\n\n{md_all}")
                furniture_records.extend(extract_furniture_records(doc))
                doc.save_as_json(json_chunk)
                export_page_markdown(doc, md_dir, range(start, end + 1))
                export_furniture_pages(doc, md_dir, range(start, end + 1))
                all_table_meta.extend(
                    export_tables(doc, tables_dir, f"{stem}_p{start:03d}_{end:03d}")
                )
                all_eq_meta.extend(
                    export_equations(doc, eq_dir, f"{stem}_p{start:03d}_{end:03d}")
                )
                conv_status = str(getattr(result, "status", ""))
                chunk_records.append(
                    {
                        "start": start,
                        "end": end,
                        "status": conv_status,
                        "wall_s": round(time.perf_counter() - t_chunk, 3),
                        "markdown": str(md_chunk),
                        "json": str(json_chunk),
                        "json_bytes": file_size(json_chunk),
                        "md_bytes": file_size(md_chunk),
                    }
                )
                LOG.info(
                    "Finished pages %s-%s in %.1fs status=%s",
                    start,
                    end,
                    time.perf_counter() - t_chunk,
                    conv_status,
                )
                del result
                gc.collect()
            except Exception as exc:
                status = "error"
                tb = traceback.format_exc()
                errors.append(f"pages {start}-{end}: {exc}\n{tb}")
                LOG.exception("Chunk %s-%s failed", start, end)
                chunk_records.append(
                    {
                        "start": start,
                        "end": end,
                        "status": "failed",
                        "error": str(exc),
                        "wall_s": round(time.perf_counter() - t_chunk, 3),
                    }
                )
                continue
    except Exception as exc:
        status = "error"
        errors.append(f"converter init/run: {exc}\n{traceback.format_exc()}")
        LOG.exception("Converter failed")

    wall_s = time.perf_counter() - t0
    full_md_path = md_dir / f"{stem}.md"
    full_md = "\n\n".join(md_parts)
    full_md_path.write_text(full_md, encoding="utf-8")
    # Body-only is the retrieval default. Keep an explicit .search.md alias
    # and an all-layer furniture archive for header/commentary detection.
    search_md_path = md_dir / f"{stem}.search.md"
    search_md_path.write_text(full_md, encoding="utf-8")
    furniture_md_path = md_dir / f"{stem}.furniture.md"
    furniture_md_path.write_text("\n\n".join(furniture_parts), encoding="utf-8")
    furniture_json_path = structured_dir / "furniture_by_page.json"
    furniture_json_path.write_text(
        json.dumps(furniture_records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    full_json_path = structured_dir / f"{stem}.json"
    if len(windows) == 1 and last_doc is not None:
        last_doc.save_as_json(full_json_path)
    else:
        bundle = {
            "schema_name": "DoclingChunkBundle",
            "name": stem,
            "source": str(pdf_path),
            "pages_converted": [list(w) for w in windows],
            "chunks": [],
        }
        for rec in chunk_records:
            jp = rec.get("json")
            if jp and Path(jp).is_file():
                bundle["chunks"].append(json.loads(Path(jp).read_text(encoding="utf-8")))
        full_json_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    (tables_dir / f"{stem}_tables_index.json").write_text(
        json.dumps(all_table_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (eq_dir / f"{stem}_all_equations.json").write_text(
        json.dumps(all_eq_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    try:
        import docling as _docling

        docling_ver = getattr(_docling, "__version__", DOCLING_VERSION)
    except Exception:
        docling_ver = DOCLING_VERSION

    meta = {
        "source_pdf": str(pdf_path),
        "output_dir": str(out_dir),
        "stem": stem,
        "pdf_pages_total": n_pages,
        "converted_page_range": [range_start, range_end],
        "chunk_pages": chunk_n,
        "windows": chunk_records,
        "status": status,
        "errors": errors,
        "started_utc": started,
        "finished_utc": _now_iso(),
        "wall_seconds": round(wall_s, 3),
        "docling_version": docling_ver,
        "python": sys.version,
        "flags": {
            **flags_record(),
            "generate_page_images": bool(args.generate_page_images),
            "images_scale": float(args.images_scale),
        },
        "model_cache_dirs": model_names_from_cache(),
        "counts": {
            "tables": len(all_table_meta),
            "equations": len(all_eq_meta),
            "windows": len(windows),
            "windows_ok": sum(
                1 for r in chunk_records if r.get("status") not in (None, "failed")
            ),
        },
        "output_sizes_bytes": {
            "markdown": file_size(full_md_path),
            "structured_json": file_size(full_json_path),
            "tables_dir": sum(f.stat().st_size for f in tables_dir.rglob("*") if f.is_file()),
            "equations_dir": sum(f.stat().st_size for f in eq_dir.rglob("*") if f.is_file()),
        },
        "paths": {
            "markdown": str(full_md_path),
            "markdown_search": str(search_md_path),
            "markdown_furniture": str(furniture_md_path),
            "furniture_json": str(furniture_json_path),
            "structured": str(full_json_path),
            "tables": str(tables_dir),
            "equations": str(eq_dir),
        },
        "markdown_layers": {
            "search": "body",
            "furniture_archive": "all",
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("Wrote %s status=%s wall=%.1fs", meta_path, status, wall_s)

    if status == "success" and not args.no_postprocess:
        try:
            from postprocess import run as postprocess_run
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from postprocess import run as postprocess_run
        try:
            LOG.info("Running postprocess.py profile=%s", args.commentary_profile)
            postprocess_run(
                out_dir,
                profile_name=args.commentary_profile,
                indexes_dir=None,       # postprocess: <root>/indexes-lite beside documents/
                pdf=pdf_path,
            )
        except Exception:
            LOG.exception("postprocess.py failed (conversion itself succeeded)")
            return 1

    return 0 if status == "success" else 1


if __name__ == "__main__":
    # Windows: a piped stdout / stderr is cp1252, which cannot print the IS symbols (Ω, →, ≤ ...) -- write UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
