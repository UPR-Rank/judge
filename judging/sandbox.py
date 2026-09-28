"""The filesystem layout the grader works with.

Two folders matter:

* the sandbox folder, one subfolder per submission, where the source is
  written, compiled and executed. It is local to the grader and is wiped
  between runs.
* the problems folder, which holds the test cases of every problem and is
  shared read-only with the web app, which needs it to accept uploads.
"""

import os
import shutil
import stat

from django.conf import settings


def get_submission_folder(submission) -> str:
    return os.path.join(settings.SANDBOX_FOLDER, str(submission.id))


def on_remove_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWUSR)
        func(path)
    except:
        pass


def remove_submission_folder(submission) -> str:
    submission_folder = get_submission_folder(submission)
    if os.path.exists(submission_folder):
        shutil.rmtree(submission_folder, onerror=on_remove_error)
    return submission_folder


def create_submission_folder(submission) -> str:
    submission_folder = remove_submission_folder(submission)
    os.makedirs(submission_folder, exist_ok=True)
    return submission_folder


def get_problem_folder(problem) -> str:
    return os.path.join(settings.PROBLEMS_FOLDER, str(problem.id))


def get_case_folders(problem):
    """Return the (inputs, outputs) folders of a problem."""
    problem_folder = get_problem_folder(problem)
    return (
        os.path.join(problem_folder, "inputs"),
        os.path.join(problem_folder, "outputs"),
    )


def check_problem_folder(problem) -> bool:
    """Whether a problem's test cases are present and usable.

    The inputs and outputs folders must both exist and hold the same number
    of cases, since the grader runs them pairwise in sorted order.
    """
    i_folder, o_folder = get_case_folders(problem)
    if not os.path.exists(i_folder) or not os.path.isdir(i_folder):
        return False
    if not os.path.exists(o_folder) or not os.path.isdir(o_folder):
        return False
    if len(os.listdir(i_folder)) != len(os.listdir(o_folder)):
        return False
    return True


def list_case_files(problem):
    """Return the (inputs, outputs) case files of a problem, both sorted.

    Both lists are sorted so that the nth input is matched with the nth
    output, the same way the judge has always paired them.
    """
    i_folder, o_folder = get_case_folders(problem)
    i_files = sorted(os.path.join(i_folder, name) for name in os.listdir(i_folder))
    o_files = sorted(os.path.join(o_folder, name) for name in os.listdir(o_folder))
    return i_files, o_files
