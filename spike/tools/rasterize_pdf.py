from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf


def main() -> int:
    parser = argparse.ArgumentParser(description="QA-only PDF rasterizer (PyMuPDF is not a runtime dependency)")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(args.pdf)
    for index, page in enumerate(document, start=1):
        pixmap = page.get_pixmap(dpi=args.dpi, alpha=False)
        pixmap.save(args.output_dir / f"page-{index:02d}.png")
    print(f"{args.pdf.name}: {document.page_count} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
