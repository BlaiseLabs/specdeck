"""Questions an engineer new to tapedeck would ask, each answerable from the code in all
three copies. Questions about test dependencies are deliberately absent: that information
exists only in the spec copy, so asking would measure "the spec has extra facts" — true by
construction — rather than "the spec makes the code easier to find your way around"."""
import re


def _has(*words):
    return lambda a: all(re.search(w, a, re.I) for w in words)


QUESTIONS = [
    ("Q1", "Which file, and which class in it, removes secret values from recorded data?",
     _has(r"serialize", r"Redactor")),
    ("Q2", "What exception type does validate_tape raise when a tape is missing a required key?",
     _has(r"ValueError")),
    ("Q3", "List every key that a tape is required to contain.",
     _has(r"session", r"timeline", r"mocked_modules", r"metadata")),
    ("Q4", "Which class blocks network access during replay, and which file is it in?",
     _has(r"NoNetwork", r"netguard")),
    ("Q5", "Name every test function (file::function) that checks a recorded tape contains no secrets.",
     _has(r"test_tape_contains_no_secrets", r"test_no_secrets_in_tape")),
    ("Q6", "Which CLI subcommand compares two tapes, and what exit code does it return when they differ?",
     lambda a: re.search(r"\bdiff\b", a, re.I) and re.search(r"\b1\b", a)),
    ("Q7", "How many levels deep does snapshot() recurse into nested values before it stops?",
     _has(r"\b6\b")),
    ("Q8", "How does tapedeck decide whether a returned value is a stream to consume? Name the function, "
           "and the property that makes a value NOT count as a stream.",
     _has(r"_is_stream", r"__getitem__|indexable|subscript")),
    ("Q9", "Which test file exercises the Slack-shaped fake SDK?",
     _has(r"test_slack_shape_tap")),
    ("Q10", "Which test (file::function) fails if inbound events stop being ordered before the outbound "
            "calls they trigger?",
     _has(r"test_timeline_orders_inbound_before_resulting_outbound")),
]
