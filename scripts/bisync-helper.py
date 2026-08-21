#!/usr/bin/env python3

import os
import re
import sys
from pathlib import Path


IGNORABLE_PERMISSION = "insufficientFilePermissions"
IGNORABLE_BLOCKED = "FileBlocked"

FATAL_MARKERS = (
    "Bisync critical error",
    "Bisync aborted",
    "Failed to create bisync",
    "invalid_grant",
    "401 Unauthorized",
    "429",
    "500 Internal",
    "quotaExceeded",
    "rateLimitExceeded",
    "context deadline exceeded",
)


ERROR_START = re.compile(
    r"ERROR\s*:\s*(.*?)\s*:\s*Failed to copy:"
)


def read_lines(path: Path):
    if not path.exists():
        return []

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()


def load_paths(path: Path):
    if not path.exists():
        return set()

    return {
        line.strip()
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        if line.strip()
    }


def save_paths(path: Path, paths):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    ordered = sorted(paths)

    path.write_text(
        "\n".join(ordered)
        + ("\n" if ordered else ""),
        encoding="utf-8",
    )


def parse_error_blocks(lines):
    """
    Convert rclone multiline error output into:

        [
            (path, complete_error_block),
            ...
        ]

    Example:

        ERROR : foo/bar.pdf: Failed to copy:
        googleapi: Error 403:
        The user does not have sufficient permissions.
        insufficientFilePermissions

    becomes one block.
    """

    blocks = []

    current_path = None
    current_lines = []

    def finish():
        nonlocal current_path
        nonlocal current_lines

        if current_path:
            blocks.append(
                (
                    current_path.strip(),
                    "\n".join(current_lines),
                )
            )

        current_path = None
        current_lines = []

    for line in lines:

        match = ERROR_START.search(line)

        if match:
            finish()

            current_path = match.group(1).strip()
            current_lines = [line]

            continue

        if current_path is None:
            continue

        # A new timestamped rclone log record terminates the
        # multiline error block.
        if re.match(
            r"^\d{4}/\d{2}/\d{2}\s+"
            r"\d{2}:\d{2}:\d{2}\s+"
            r"(INFO|NOTICE|ERROR|DEBUG|WARNING|"
            r"Transferred|Checks|Elapsed|Transferred:)",
            line,
        ):
            finish()
        else:
            current_lines.append(line)

    finish()

    return blocks


def classify_block(block):
    """
    Return:
        permission
        blocked
        fatal
    """

    if IGNORABLE_PERMISSION in block:
        return "permission"

    if IGNORABLE_BLOCKED in block:
        return "blocked"

    return "fatal"


def parse_log(
    log_file: Path,
    ignore_file: Path,
    report_file: Path,
    result_file: Path,
):
    lines = read_lines(log_file)

    existing = load_paths(ignore_file)

    if not lines:
        ignore_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        ignore_file.touch()

        report_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_file.write_text(
            "No bisync log found.\n",
            encoding="utf-8",
        )

        write_result(
            result_file,
            new_ignored=0,
            ignored_total=len(existing),
            has_unignorable=True,
        )

        return

    full_log = "\n".join(lines)

    blocks = parse_error_blocks(lines)

    new_ignored = []
    reports = []

    has_unignorable = False

    # ------------------------------------------------------------
    # Global fatal markers.
    #
    # These can appear outside Failed-to-copy blocks.
    # ------------------------------------------------------------

    for marker in FATAL_MARKERS:
        if marker in full_log:
            has_unignorable = True
            break

    # ------------------------------------------------------------
    # Inspect every Failed-to-copy block.
    #
    # If ANY block isn't explicitly ignorable,
    # the whole run is unsafe for automatic resync.
    # ------------------------------------------------------------

    for path, block in blocks:

        classification = classify_block(block)

        if classification == "fatal":
            has_unignorable = True
            continue

        path = path.strip()

        if not path:
            has_unignorable = True
            continue

        if path not in existing:
            existing.add(path)
            new_ignored.append(path)

        if classification == "permission":
            reports.append(
                f"[insufficientFilePermissions] {path}"
            )

        elif classification == "blocked":
            reports.append(
                f"[FileBlocked] {path}"
            )

    # ------------------------------------------------------------
    # Save current ignored list.
    # ------------------------------------------------------------

    save_paths(
        ignore_file,
        existing,
    )

    # ------------------------------------------------------------
    # Save human-readable report.
    # ------------------------------------------------------------

    report_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_lines = [
        "Google Drive <-> Koofr bisync ignored-file report",
        "",
        f"Total currently ignored: {len(existing)}",
        f"Newly detected this run: {len(new_ignored)}",
        f"Unignorable errors found: {'YES' if has_unignorable else 'NO'}",
        "",
    ]

    if reports:
        report_lines.append(
            "Ignorable errors detected this run:"
        )

        report_lines.extend(
            sorted(set(reports))
        )

    else:
        report_lines.append(
            "No ignorable errors detected this run."
        )

    if has_unignorable:
        report_lines.extend(
            [
                "",
                "IMPORTANT:",
                "At least one error was not classified as safely ignorable.",
                "Automatic resync is therefore disabled for this run.",
            ]
        )

    report_file.write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )

    # ------------------------------------------------------------
    # Console output.
    # ------------------------------------------------------------

    print("=" * 60)
    print("BISYNC ERROR PARSER")
    print("=" * 60)

    print(
        f"Total ignored files : {len(existing)}"
    )

    print(
        f"New ignored files   : {len(new_ignored)}"
    )

    print(
        "Unignorable errors  : "
        + ("YES" if has_unignorable else "NO")
    )

    print()

    if new_ignored:
        print("NEW IGNORED FILES:")

        for path in sorted(new_ignored):
            print(
                f"  {path}"
            )

    else:
        print("No new ignored files.")

    print()

    if has_unignorable:
        print(
            "WARNING: automatic retry/resync is BLOCKED."
        )

    write_result(
        result_file,
        new_ignored=len(new_ignored),
        ignored_total=len(existing),
        has_unignorable=has_unignorable,
    )


def write_result(
    result_file: Path,
    new_ignored: int,
    ignored_total: int,
    has_unignorable: bool,
):
    result_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result_file.write_text(
        "\n".join(
            [
                f"new_ignored={new_ignored}",
                f"ignored_total={ignored_total}",
                (
                    "has_unignorable_errors="
                    + ("true" if has_unignorable else "false")
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    github_output = os.environ.get(
        "GITHUB_OUTPUT"
    )

    if github_output:
        with open(
            github_output,
            "a",
            encoding="utf-8",
        ) as output:

            output.write(
                f"new_ignored={new_ignored}\n"
            )

            output.write(
                f"ignored_total={ignored_total}\n"
            )

            output.write(
                "has_unignorable_errors="
                + (
                    "true"
                    if has_unignorable
                    else "false"
                )
                + "\n"
            )


def parse_retry(
    log_file: Path,
    ignore_file: Path,
    count_file: Path,
):
    lines = read_lines(log_file)

    existing = load_paths(ignore_file)

    if not lines:
        count_file.write_text(
            "0\n",
            encoding="utf-8",
        )
        return

    blocks = parse_error_blocks(lines)

    new_paths = []

    for path, block in blocks:

        classification = classify_block(block)

        if classification not in (
            "permission",
            "blocked",
        ):
            continue

        path = path.strip()

        if not path:
            continue

        if path not in existing:
            existing.add(path)
            new_paths.append(path)

    save_paths(
        ignore_file,
        existing,
    )

    count_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    count_file.write_text(
        str(len(new_paths)) + "\n",
        encoding="utf-8",
    )

    print("=" * 60)
    print("RETRY ERROR PARSER")
    print("=" * 60)

    print(
        f"New ignored files from retry: {len(new_paths)}"
    )

    for path in sorted(new_paths):
        print(
            f"  {path}"
        )


def build_filter(
    ignore_file: Path,
    filter_file: Path,
):
    paths = load_paths(ignore_file)

    filter_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    lines = []

    for path in sorted(paths):
        lines.append(
            f"- /{path}"
        )

    filter_file.write_text(
        "\n".join(lines)
        + ("\n" if lines else ""),
        encoding="utf-8",
    )

    print("=" * 60)
    print("RCLONE FILTER")
    print("=" * 60)

    if lines:
        print(
            "\n".join(lines)
        )
    else:
        print("(none)")


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:",
            file=sys.stderr,
        )
        print(
            "  bisync-helper.py build-filter <ignored> <filter>",
            file=sys.stderr,
        )
        print(
            "  bisync-helper.py parse <log> <ignored> <report> <result>",
            file=sys.stderr,
        )
        print(
            "  bisync-helper.py parse-retry <log> <ignored> <count>",
            file=sys.stderr,
        )
        sys.exit(2)

    command = sys.argv[1]

    if command == "build-filter":

        if len(sys.argv) != 4:
            sys.exit(2)

        build_filter(
            Path(sys.argv[2]),
            Path(sys.argv[3]),
        )

    elif command == "parse":

        if len(sys.argv) != 6:
            sys.exit(2)

        parse_log(
            Path(sys.argv[2]),
            Path(sys.argv[3]),
            Path(sys.argv[4]),
            Path(sys.argv[5]),
        )

    elif command == "parse-retry":

        if len(sys.argv) != 5:
            sys.exit(2)

        parse_retry(
            Path(sys.argv[2]),
            Path(sys.argv[3]),
            Path(sys.argv[4]),
        )

    else:
        print(
            f"Unknown command: {command}",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()