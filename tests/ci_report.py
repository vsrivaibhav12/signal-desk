"""Turns a pytest JUnit report into GitHub annotations, so a failed CI run says which tests failed and why on the run's summary page.

    python tests/ci_report.py report.xml

GitHub shows at most ten annotations for a step, so the first ten failures are listed and the rest are counted. Exit code is always 0:
the workflow keeps pytest's own exit status.
"""
import sys
import xml.etree.ElementTree as ET


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def failures(path: str):
    out = []
    for case in ET.parse(path).getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                text = (node.get("message") or "") + "\n" + (node.text or "")
                lines = [ln for ln in text.splitlines() if ln.strip()]
                out.append((f"{case.get('classname')}::{case.get('name')}", "\n".join(lines[-14:])[:1800]))
    return out


def main(path: str) -> None:
    try:
        found = failures(path)
    except (OSError, ET.ParseError) as exc:
        print(f"::warning::No test report could be read ({type(exc).__name__}). The run probably stopped before the tests finished.")
        return
    for name, message in found[:10]:
        print(f"::error title=FAILED {escape(name)}::{escape(message)}")
    if len(found) > 10:
        print(f"::warning::{len(found) - 10} more test failures are not listed here.")
    print(f"{len(found)} test failure(s) in the report.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "report.xml")
