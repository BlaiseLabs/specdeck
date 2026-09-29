"""Light editing from the viewer: only string literals change, and nothing is written blind."""
import textwrap

import pytest
from specdeck import and_, given, scenario, then, when
from specdeck import editing

SOURCE = textwrap.dedent('''\
    """Checkout."""
    import pytest
    from specdeck import scenario, given, when, then


    @scenario("pay", tags=["money"])
    def test_pay():
        """Paying marks the order paid.

        Card payments only.
        """
        with given('an unpaid order'):  # 注文 — keep this comment
            order = {"paid": False}
        with when("the customer pays", expected="200 OK"):
            order["paid"] = True
        with then(f"the order is {'paid'}"):
            assert order["paid"]


    def test_plain():
        with then("it works"):
            assert True
''')


@pytest.fixture
def test_file(tmp_path):
    p = tmp_path / "test_checkout.py"
    p.write_text(SOURCE, encoding="utf-8")
    return p


def _fp(path, fn="test_pay"):
    return editing.load(path, fn)["fingerprint"]


@scenario("edit-load-model", tags=["editing"])
def test_the_editor_sees_what_can_be_edited(test_file):
    """The editor form shows each field, and marks computed text as read-only."""
    with when("the test is loaded for editing"):
        m = editing.load(test_file, "test_pay")
    with then("title, details, tags and steps come from the source",
              expected="title from the docstring, 3 steps"):
        assert m["title"]["value"] == "Paying marks the order paid."
        assert m["details"]["value"] == "Card payments only."
        assert m["tags"]["value"] == ["money"]
        assert [s["keyword"] for s in m["steps"]] == ["Given", "When", "Then"]
    with and_("the f-string step is read-only, the others editable"):
        assert [s["text_editable"] for s in m["steps"]] == [True, True, False]


@scenario("edit-step-text", tags=["editing"], depends_on=["edit-load-model"])
def test_editing_a_step_changes_only_that_string(test_file):
    """Editing a step rewrites that one string literal, keeping its quote style and the rest of the line."""
    with when("the Given text is edited on a line that also has a Japanese comment"):
        res = editing.apply(test_file, "test_pay", _fp(test_file),
                            {"steps": [{"index": 0, "text": "an unpaid order for ¥1,200"}]})
        text = test_file.read_text(encoding="utf-8")
    with then("the new text is in single quotes like before, and the comment is intact",
              expected="with given('an unpaid order for ¥1,200'):  # 注文 — keep this comment"):
        assert "with given('an unpaid order for ¥1,200'):  # 注文 — keep this comment" in text
    with and_("nothing else in the file changed"):
        assert text.replace("an unpaid order for ¥1,200", "an unpaid order") == SOURCE
        added = [l for l in res["diff"].splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert res["written"] and len(added) == 1


@scenario("edit-expected-label", tags=["editing"], depends_on=["edit-step-text"])
def test_expected_labels_can_be_changed_added_and_removed(test_file):
    """Expected labels can be changed, added to a step, or removed."""
    with when("one label is removed and another is added"):
        editing.apply(test_file, "test_pay", _fp(test_file), {"steps": [
            {"index": 1, "expected": ""},
            {"index": 0, "expected": "unpaid"},
        ]})
        text = test_file.read_text()
    with then("the source reflects both", expected="given(..., expected='unpaid'); when(...) without expected"):
        assert "with given('an unpaid order', expected='unpaid'):" in text
        assert 'with when("the customer pays"):' in text


@scenario("edit-docstring", tags=["editing"], depends_on=["edit-load-model"])
def test_title_and_details_rewrite_the_docstring(test_file):
    """Title and details are the docstring's first paragraph and the rest."""
    with when("both are edited"):
        editing.apply(test_file, "test_pay", _fp(test_file),
                      {"title": "Paying marks the order as paid.", "details": "Cards and bank transfer.\nNo cash."})
        m = editing.load(test_file, "test_pay")
    with then("the docstring reads back the same way", expected="new title and two-line details"):
        assert m["title"]["value"] == "Paying marks the order as paid."
        assert m["details"]["value"] == "Cards and bank transfer.\nNo cash."
    with and_("a test without a docstring gets one inserted"):
        editing.apply(test_file, "test_plain", _fp(test_file, "test_plain"), {"title": "It works."})
        assert editing.load(test_file, "test_plain")["title"]["value"] == "It works."


@scenario("edit-tags-and-dependencies", tags=["editing"], depends_on=["edit-load-model"])
def test_tags_and_dependencies_edit_the_scenario_marker(test_file, pytester):
    """Tags and depends_on edit the @scenario marker; a plain test gets one, with its import, and still runs."""
    with when("tags change on the marked test and a dependency is added to the plain one"):
        editing.apply(test_file, "test_pay", _fp(test_file), {"tags": ["money", "checkout"], "depends_on": []})
        editing.apply(test_file, "test_plain", _fp(test_file, "test_plain"), {"depends_on": ["pay"]})
        text = test_file.read_text()
    with then("the marker lists are updated in place", expected='@scenario("pay", tags=["money", "checkout"])'):
        assert '@scenario("pay", tags=["money", "checkout"])' in text
    with and_("the plain test got a marker (scenario was already imported)"):
        assert '@scenario(depends_on=["pay"])\ndef test_plain():' in text
    with and_("the edited file still runs, with the new dependency honoured"):
        pytester.makepyfile(test_checkout=text)
        result = pytester.runpytest("-p", "no:specdeck", "-p", "specdeck.plugin")
        result.assert_outcomes(passed=2)


@scenario("edit-adds-import", tags=["editing"], depends_on=["edit-tags-and-dependencies"])
def test_adding_a_marker_adds_its_import_when_missing(tmp_path):
    """If the file doesn't import scenario yet, adding a marker adds the import too."""
    with given("a test file with no specdeck import"):
        p = tmp_path / "test_x.py"
        p.write_text("import os\n\n\ndef test_x():\n    assert os\n")
    with when("a tag is added"):
        editing.apply(p, "test_x", _fp(p, "test_x"), {"tags": ["smoke"]})
    with then("the import and the marker are both there", expected="from specdeck import scenario"):
        assert p.read_text() == ('import os\nfrom specdeck import scenario\n\n\n'
                                 '@scenario(tags=["smoke"])\ndef test_x():\n    assert os\n')


@scenario("edit-refuses-stale-or-computed", tags=["editing", "safety"], depends_on=["edit-load-model"])
def test_stale_forms_and_computed_text_are_refused(test_file):
    """An edit made against an old version of the test, or to computed text, is refused and nothing is written."""
    with given("a fingerprint taken before someone else changed the test"):
        fp = _fp(test_file)
        test_file.write_text(SOURCE.replace("the customer pays", "the customer pays twice"))
        before = test_file.read_text()
    with when("an edit is sent with the old fingerprint"):
        with pytest.raises(editing.Conflict) as err:
            editing.apply(test_file, "test_pay", fp, {"steps": [{"index": 0, "text": "x"}]})
    with then("it is refused as a conflict and the file is untouched", expected="409"):
        assert err.value.status == 409 and test_file.read_text() == before
    with and_("editing the f-string step is refused as computed"):
        with pytest.raises(editing.EditError, match="computed"):
            editing.apply(test_file, "test_pay", _fp(test_file), {"steps": [{"index": 2, "text": "x"}]})


@scenario("edit-dry-run", tags=["editing", "safety"], depends_on=["edit-step-text"])
def test_review_shows_a_diff_without_writing(test_file):
    """'Review changes' returns the diff and writes nothing."""
    with when("an edit is previewed"):
        res = editing.apply(test_file, "test_pay", _fp(test_file),
                            {"steps": [{"index": 1, "text": "the customer pays by card"}]}, dry_run=True)
    with then("the diff shows the change", expected='+    with when("the customer pays by card", ...)'):
        assert '+    with when("the customer pays by card", expected="200 OK"):' in res["diff"]
    with and_("the file is unchanged"):
        assert test_file.read_text() == SOURCE and not res["written"]
