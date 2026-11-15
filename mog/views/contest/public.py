import io
import zipfile

from django.urls import reverse

from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from api.lib.queries import calculate_standing
from api.models import (
    Compiler,
    Contest,
    Result,
    Submission,
    Team,
)

from mog.forms import (
    ClarificationForm,
)
from mog.gating import (
    user_can_bypass_frozen_in_contest,
    user_is_admin,
)
from mog.helpers import filter_submissions, get_paginator
from mog.model_helpers.contest import can_create_problem_in_contest

from ..submission import get_submission


def contests(request):
    running, coming, past = Contest.get_all_contests(request.user)
    return render(
        request,
        "mog/contest/index.html",
        {
            "running_contests": running,
            "coming_contests": coming,
            "past_contests": past,
        },
    )


def contest_clarifications(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    if not contest.can_be_seen_by(request.user):
        raise Http404()

    if user_is_admin(request.user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    clarifications = contest.visible_clarifications(request.user)

    if request.user.is_authenticated:
        for clarification in clarifications:
            clarification.seen.add(request.user)
    return render(
        request,
        "mog/contest/clarifications.html",
        {
            "contest": contest,
            "clarifications": clarifications,
            "form": ClarificationForm(contest=contest),
        },
    )


def contest_overview(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not contest.overview_can_be_seen_by(request.user):
        raise Http404()

    if user_is_admin(request.user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    return render(
        request,
        "mog/contest/overview.html",
        {
            "contest": contest,
        },
    )


def contest_problems(request, contest_id):
    contest, user = get_object_or_404(Contest, pk=contest_id), request.user

    if not contest.can_be_seen_by(user):
        return redirect(reverse("mog:contest_overview", args=(contest.pk,)))

    if user_is_admin(user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    return render(
        request,
        "mog/contest/problems.html",
        {
            "contest": contest,
            "problems": contest.problems.order_by("position"),
            "can_create_problem": can_create_problem_in_contest(user, contest),
        },
    )


@require_http_methods(["GET"])
def contest_standing(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    if not contest.can_be_seen_by(request.user):
        raise Http404()

    user_instance = None
    if request.user.is_authenticated:
        user_instance = contest.registration(request.user)

    if contest.is_running:
        show_virtual = False
        show_virtual_checkbox = False
    elif user_instance and user_instance.is_running:
        show_virtual = not user_instance.real
        show_virtual_checkbox = False
    else:
        show_virtual = request.GET.get("show_virtual") == "on"
        show_virtual_checkbox = True

    group_in_ranking = request.GET.get("group", None)
    if not group_in_ranking and user_instance and not user_instance.real:
        # by default in virtual participation show all teams in one group
        group_names = [None]
    elif group_in_ranking == "<all>":
        group_names = contest.group_names(include_virtual=show_virtual) or [None]
    elif group_in_ranking == "<one>":
        group_names = [None]
    else:
        group_names = [group_in_ranking]

    # get ranking for every group
    ranking_groups = []
    for group in group_names:
        problems, instance_results = calculate_standing(
            contest,
            show_virtual,
            user_instance,
            group,
            user_can_bypass_frozen_in_contest(request.user, contest),
        )
        ranking_groups.append(
            {
                "group": group,
                "problems": problems,
                "instance_results": instance_results,
                "solved": sum(ir.solved for ir in instance_results),
                "penalty": sum(ir.penalty for ir in instance_results),
                "instances": len(instance_results),
            }
        )

        def key(group):
            if user_instance and group["group"] == user_instance.group:
                return -1e9, 0
            if group["group"] == contest.group:
                return 1e6, 0
            return -group["solved"], group["penalty"]

        ranking_groups = sorted(ranking_groups, key=key)

    return render(
        request,
        "mog/contest/standing.html",
        {
            "contest": contest,
            "ranking_groups": ranking_groups,
            "show_virtual": show_virtual,
            "user_instance": user_instance,
            "group_names": contest.group_names(include_virtual=show_virtual),
            "group_in_ranking": group_in_ranking,
            "show_virtual_checkbox": show_virtual_checkbox,
        },
    )


@require_http_methods(["GET"])
def contest_submissions(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    if not contest.can_be_seen_by(request.user):
        raise Http404()
    submission_list, query = filter_submissions(
        request.user,
        problem=request.GET.get("problem"),
        problem_exact=True,
        contest=contest.id,
        username=request.GET.get("username"),
        result=request.GET.get("result"),
        language=request.GET.get("language"),
    )
    submissions = get_paginator(submission_list, 30, request.GET.get("page"))
    return render(
        request,
        "mog/contest/submissions.html",
        {
            "contest": contest,
            "submissions": submissions,
            "results": Result.get_all_results(),
            "languages": Compiler.get_all_languages(),
            "query": query,
        },
    )


@require_http_methods(["GET"])
def contest_rating_changes(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not contest.is_past or not contest.rated:
        raise Http404()

    return render(
        request,
        "mog/contest/rating_changes.html",
        {
            "contest": contest,
            "rating_changes": contest.rating_changes.all().order_by("rank"),
        },
    )


@require_http_methods(["GET"])
def team_submissions(request, contest_id, team_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    team = get_object_or_404(Team, pk=team_id)

    if not contest.can_be_seen_by(request.user):
        raise Http404()

    profile = team.profiles.first()

    if not profile:
        raise Http404()

    return redirect(
        reverse("mog:contest_submissions", args=(contest_id,))
        + "?username="
        + profile.user.username
    )


@require_http_methods(["GET"])
def contest_submissions_export(request, contest_id):
    """Export ZIP with all submission of a contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    instances_id = contest.instances.values_list("id")
    problems = contest.get_problems

    try:
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as zipObj:
            for problem in problems:
                header = "submissions_%s/%s" % (contest.name, problem.letter)
                submissions = Submission.objects.filter(
                    problem_id=problem.id, instance_id__in=instances_id
                )
                for submission in submissions:
                    filename, source = get_submission(submission)
                    zipObj.writestr("%s/%s" % (header, filename), source)
        zip_submissions = content.getvalue()
        response = HttpResponse(zip_submissions, content_type="application/zip")
        response[
            "Content-Disposition"
        ] = 'attachment; filename="submissions_{0}.zip"'.format(contest.name)
        return response

    except Exception as e:
        msg = _("Error exporting submissions: " + str(e))
        messages.error(request, msg, extra_tags="danger")
        return HttpResponseForbidden()
