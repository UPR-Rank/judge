"""The grader: a long-running process that judges pending submissions.

Run it with ``manage.py grader``. It is a single process that polls the
database, claims one submission at a time, and grades it inside the sandbox.
It is replaced by a Celery worker in a later sprint; until then this is the
only thing that turns submissions into verdicts.
"""

import logging as log
import time

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections

from judging import state
from judging.compilers import compile_submission
from judging.sandbox import (
    check_problem_folder,
    create_submission_folder,
    get_submission_folder,
    remove_submission_folder,
)
from judging.service import grade_submission


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
                    # ready to grade the new submission
                    create_submission_folder(submission)
                    if check_problem_folder(submission.problem):
                        if compile_submission(submission):
                            grade_submission(submission, number_of_executions)
                    else:
                        log.error(
                            "There was a problem with the problem folder %s for submission #%d",
                            get_submission_folder(submission),
                            submission.id,
                        )
                        state.set_internal_error(
                            submission, "internal error, problem not ready"
                        )
                    if not settings.DEBUG:
                        # If we're in DEBUG mode, leave the submission folder
                        # to make debugging easier.
                        remove_submission_folder(submission)
                else:
                    # we only wait if there was no submission to grade
                    time.sleep(sleep)
            except DatabaseError as e:
                # Grading failed, database error caught here
                # Possible reasons:
                # 1) The connection to the database was interrupted or could not be established
                # 2) Raise condition in a trigger in the database
                log.error("Unexpected database error: %s", str(e))
                close_old_connections()
                if submission:
                    # The submission was already claimed, so put it back in the
                    # queue instead of leaving it stuck in compiling.
                    state.reset_to_pending(submission.id)
