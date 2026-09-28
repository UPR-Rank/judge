"""Make sure the results the judge and the reaper need exist.

A submission is only ever handed out as Pending, Compiling, Running or one
of the verdicts, and the grader looks those rows up by name every time it
claims something. Nothing created them automatically: they came from a
fixture that has to be loaded by hand, so an installation that skipped it
had a grader that died with Result.DoesNotExist the first time it tried to
grade anything.

Creating them is idempotent, so an installation that already loaded the
fixture keeps its own rows and only gains the ones it was missing.
"""

from django.db import migrations

RESULTS = [
    # (name, color, penalty) — same values as api/fixtures/results.json.
    ("Accepted", "green", False),
    ("Wrong Answer", "red", True),
    ("Time Limit Exceeded", "red", True),
    ("Memory Limit Exceeded", "red", True),
    ("Pending", "orange", False),
    ("Running", "orange", False),
    ("Compiling", "orange", False),
    ("Runtime Error", "red", True),
    ("Compilation Error", "blue", False),
    ("Internal Error", "blue", False),
    ("Disqualified", "red", True),
    ("Idleness Limit Exceeded", "red", True),
]


def create_results(apps, schema_editor):
    Result = apps.get_model("api", "Result")
    for name, color, penalty in RESULTS:
        Result.objects.get_or_create(
            name__iexact=name,
            defaults={"name": name, "color": color, "penalty": penalty},
        )


def nothing(apps, schema_editor):
    # The rows are not touched on the way back: a submission may already
    # point at them, and deleting them would orphan it.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0049_submission_claimed_at"),
    ]

    operations = [
        migrations.RunPython(create_results, nothing),
    ]
