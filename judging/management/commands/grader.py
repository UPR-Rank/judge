"""The grader: a long-running process that judges pending submissions.

Run it with ``manage.py grader``. It is a single process that polls the
database, claims one submission at a time and grades it inside the sandbox.

Celery workers (``judging.tasks.grade_submission``) now do the same work
through a queue, and that is what the compose files start. This command is
kept because the claim is atomic, so a worker and this command can run at
the same time without grading anything twice, and because it is the
simplest way to drain a queue by hand.
"""

import logging as log
import time

from django.core.management import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections

from judging import state
from judging.service import grade_claimed_submission


class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super(Command, self).__init__(*args, **kwargs)

    def add_arguments(self, parser):
        parser.add_argument(
            "--sleep",
            type=int,
            default="5",
            help="Number of seconds to sleep between grade submissions.",
        )
        parser.add_argument(
            "--number_of_executions",
            type=int,
            default="2",
            help="Number of executions to prevent TLE",
        )

    def handle(self, *args, **options):
        verbosity = {0: log.WARN, 1: log.INFO, 2: log.DEBUG, 3: log.DEBUG}
        log.basicConfig(
            format="%(levelname)s - %(message)s",
            level=verbosity.get(options["verbosity"], log.INFO),
        )
        sleep = options.get("sleep")
        number_of_executions = options.get("number_of_executions")
        # validate input
        if sleep <= 0:
            raise CommandError("sleep argument must to be positive")
        if number_of_executions < 1:
            raise CommandError("number_of_executions must to be a positive integer")
        # store compilers
        while True:
            submission = None
            try:
                # Takes the first available pending submission and changes its
                # status to compiling, atomically, so that no other grader can
                # take it. Returns None when there is nothing to grade.
                submission = state.claim_next_pending_submission()

                if submission:
                    grade_claimed_submission(submission, number_of_executions)
                else:
                    # we only wait if there was no submission to grade
                    time.sleep(sleep)
            except DatabaseError as e:
                # Grading failed, database error caught here
                # Possible reasons:
                # 1) The connection to the database was interrupted or could not be established
                # 2) Raise condition in a trigger in the database (TODO: Fix this raise condition)
                # TODO: Add more logs!
                log.error("Unexpected database error: %s", str(e))
                close_old_connections()
                if submission:
                    # The submission was already claimed, so put it back in the
                    # queue instead of leaving it stuck in compiling.
                    state.reset_to_pending(submission.id)
