from __future__ import annotations

import argparse
import json
from pathlib import Path

from .converters import LibreOfficeConverter, WordComConverter
from .pipeline import OFFICIAL_GENERATION_SIMULATION, TEMPLATE_VALIDATION_TEST, generate


DEFAULT_CONVERTER = "libreoffice"


def create_converter(name: str = DEFAULT_CONVERTER):
    return WordComConverter() if name == "word" else LibreOfficeConverter()


def main() -> int:
    parser = argparse.ArgumentParser(description="ICP Renov isolated DOCX/PDF pipeline spike")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("spike/output"))
    parser.add_argument("--converter", choices=("word", "libreoffice"), default=DEFAULT_CONVERTER)
    parser.add_argument(
        "--mode", choices=("validation-test", "official-simulation"), default="validation-test",
        help="TO_VALIDATE test artifact or AVAILABLE-only official-effect simulation",
    )
    args = parser.parse_args()
    converter = create_converter(args.converter)
    mode = TEMPLATE_VALIDATION_TEST if args.mode == "validation-test" else OFFICIAL_GENERATION_SIMULATION
    result = generate(args.template, args.fixture, args.output, converter, mode)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
