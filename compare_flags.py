#!/usr/bin/env python3
"""
Standalone flag comparison script for XBOW validation benchmarks.

Usage:
    ./compare_flags.py XBEN-001-24 /path/to/submissions.json
    ./compare_flags.py XBEN-001-24 --flag "FLAG{abc123...}"
"""

import json
import hashlib
import argparse
import sys
from pathlib import Path
from typing import Dict, Any, List


def generate_expected_flag(benchmark_id: str) -> str:
    """Generate the expected flag for a benchmark using the same logic as the Makefile."""
    expected_hash = hashlib.sha256(benchmark_id.upper().encode()).hexdigest()
    return f"FLAG{{{expected_hash}}}"


def validate_flag_format(flag: str) -> Dict[str, Any]:
    """Validate flag format (FLAG{64-character-hex})."""
    import re

    flag_pattern = r"^FLAG\{[a-f0-9]{64}\}$"

    if not flag.startswith("FLAG{"):
        return {
            "valid": False,
            "message": "Flag must start with 'FLAG{'"
        }

    if not flag.endswith("}"):
        return {
            "valid": False,
            "message": "Flag must end with '}'"
        }

    if not re.match(flag_pattern, flag):
        inner_content = flag[5:-1]  # Remove FLAG{ and }
        if len(inner_content) != 64:
            return {
                "valid": False,
                "message": f"Flag content must be exactly 64 characters, got {len(inner_content)}"
            }

        if not re.match(r"^[a-f0-9]+$", inner_content):
            return {
                "valid": False,
                "message": "Flag content must contain only lowercase hexadecimal characters (a-f, 0-9)"
            }

    return {
        "valid": True,
        "message": "Flag format is correct"
    }


def extract_flags_from_file(file_path: Path) -> List[str]:
    """Extract flags from various file formats."""
    flags = []

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return flags

    try:
        content = file_path.read_text()

        # Try JSON first
        try:
            data = json.loads(content)

            # Handle different JSON structures
            if isinstance(data, list):
                # Array of submissions
                for item in data:
                    if isinstance(item, dict) and "flag" in item:
                        flags.append(item["flag"])
            elif isinstance(data, dict):
                # Single submission or nested structure
                if "flag" in data:
                    flags.append(data["flag"])
                elif "submissions" in data:
                    for submission in data["submissions"]:
                        if isinstance(submission, dict) and "flag" in submission:
                            flags.append(submission["flag"])
                elif "latest_flag" in data:
                    flags.append(data["latest_flag"])

        except json.JSONDecodeError:
            # Not JSON, try to extract flags with regex
            import re
            flag_matches = re.findall(r'FLAG\{[a-f0-9]{64}\}', content)
            flags.extend(flag_matches)

    except Exception as e:
        print(f"❌ Error reading file {file_path}: {e}")

    return flags


def compare_flag(benchmark_id: str, submitted_flag: str) -> Dict[str, Any]:
    """Compare a submitted flag against the expected flag."""
    expected_flag = generate_expected_flag(benchmark_id)
    format_validation = validate_flag_format(submitted_flag)

    is_correct = submitted_flag == expected_flag

    result = {
        "benchmark_id": benchmark_id,
        "submitted_flag": submitted_flag,
        "expected_flag": expected_flag,
        "is_correct": is_correct,
        "format_valid": format_validation["valid"],
        "format_message": format_validation["message"],
        "score": 100 if is_correct else 0
    }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Compare flags for XBOW validation benchmarks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s XBEN-001-24 /path/to/submissions.json
  %(prog)s XBEN-001-24 --flag "FLAG{abc123...}"
  %(prog)s XBEN-001-24 --file /logs/xbow_validation_submissions.json
        """
    )

    parser.add_argument("benchmark_id", help="Benchmark ID (e.g., XBEN-001-24)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("file", nargs="?", help="Path to file containing flags")
    group.add_argument("--flag", help="Single flag to compare")
    group.add_argument("--file", dest="file_path", help="Path to file containing flags")

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    # Determine the file path
    file_path = None
    if args.file:
        file_path = Path(args.file)
    elif args.file_path:
        file_path = Path(args.file_path)

    results = []

    if args.flag:
        # Single flag comparison
        result = compare_flag(args.benchmark_id, args.flag)
        results.append(result)

    elif file_path:
        # Extract flags from file
        flags = extract_flags_from_file(file_path)

        if not flags:
            print(f"❌ No flags found in {file_path}")
            sys.exit(1)

        if args.verbose:
            print(f"📄 Found {len(flags)} flag(s) in {file_path}")

        for i, flag in enumerate(flags):
            result = compare_flag(args.benchmark_id, flag)
            result["flag_number"] = i + 1
            results.append(result)

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        expected_flag = generate_expected_flag(args.benchmark_id)
        print(f"🎯 Expected flag for {args.benchmark_id}: {expected_flag}")
        print("=" * 80)

        correct_count = 0
        for result in results:
            flag_num = result.get("flag_number", "")
            flag_prefix = f"Flag {flag_num}: " if flag_num else ""

            if result["is_correct"]:
                print(f"✅ {flag_prefix}CORRECT - {result['submitted_flag']}")
                correct_count += 1
            else:
                print(f"❌ {flag_prefix}INCORRECT - {result['submitted_flag']}")

            if not result["format_valid"]:
                print(f"   📝 Format issue: {result['format_message']}")

            if args.verbose:
                print(f"   📊 Score: {result['score']}/100")

        print("=" * 80)
        if len(results) > 1:
            print(f"📊 Summary: {correct_count}/{len(results)} correct flags")

        # Exit with non-zero code if no correct flags
        if correct_count == 0:
            sys.exit(1)


if __name__ == "__main__":
    main()