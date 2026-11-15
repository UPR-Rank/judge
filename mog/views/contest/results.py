import csv
import json

from django.urls import reverse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.translation import gettext as trans
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from api.lib.queries import calculate_standing
from api.models import (
    Contest,
    RatingChange,
    Submission,
)

from mog.gating import (
    user_is_admin,
)
from mog.helpers import get_contest_json
from mog.ratings import set_ratings
from mog.statistics import get_contest_stats
from mog.templatetags.filters import format_minutes

from mog.baylor.utils import ICPCID_GUEST_PREFIX


@login_required
@require_http_methods(["POST"])
def contest_saris(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not contest.can_show_saris_to(request.user):
        return HttpResponseForbidden()

    group = request.POST.get("group", None)

    result = get_contest_json(contest, group)

    return render(
        request,
        "mog/contest/saris.html",
        {"contest": contest, "json": json.dumps(result)},
    )


@login_required
@require_http_methods(["GET"])
def download_json_saris(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not contest.can_show_saris_to(request.user):
        return HttpResponseForbidden()

    group = request.GET.get("group", None)

    result = get_contest_json(contest, group)

    response = JsonResponse(data=result, safe=True)
    response["Content-Disposition"] = 'attachment; filename="saris_{0}.json"'.format(
        contest.name
    )
    return response


@login_required
@require_http_methods(["POST"])
def rate_contest(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    next = request.POST.get("next") or reverse(
        "mog:contest_problems", args=(contest.id,)
    )
    force_rate = request.POST.get("force_rate", False)

    if contest.allow_teams:
        msg = _("This contest cannot be rated because it's open for teams.")
        messages.warning(request, msg, extra_tags="warning")
        return redirect(next)

    if not force_rate and contest.rated:
        msg = _(
            "This contest have ben rated. If you want rate again, then force the operation."
        )
        messages.info(request, msg, extra_tags="info")
        return redirect(next)

    if (
        RatingChange.objects.filter(
            contest__rated=True, contest__start_date__gt=contest.start_date
        ).count()
        > 0
    ):
        msg = _("This contest cannot be rated because it's before a rated contest.")
        messages.success(request, msg, extra_tags="warning")
        return redirect(next)

    if set_ratings(contest):
        msg = _("This contest has been rated successfully")
        messages.success(request, msg, extra_tags="success")
    else:
        msg = _("The rating-changes results are not consistent")
        messages.info(request, msg, extra_tags="info")

    return redirect(next)


@login_required
@require_http_methods(["POST"])
def unrate_contest(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    next = request.POST.get("next") or reverse(
        "mog:contest_problems", args=(contest.id,)
    )
    if (
        RatingChange.objects.filter(
            contest__rated=True, contest__start_date__gt=contest.start_date
        ).count()
        > 0
    ):
        msg = _("This contest cannot be unrated because it's before a rated contest")
        messages.warning(request, msg, extra_tags="warning")
        return redirect(next)
    contest.rating_changes.all().delete()
    contest.rated = False
    contest.save()
    msg = _("This contest have been unrated successfully")
    messages.success(request, msg, extra_tags="success")
    return redirect(next)


@login_required
@require_http_methods(["POST"])
def unfreeze_contest(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)

    if contest.is_past:
        updated = Submission.objects.filter(
            Q(problem__contest=contest) & ~Q(status="normal")
        ).update(status="normal")
        contest.needs_unfreeze = False
        contest.save()
        msg = "%s %d %s" % (
            _("Contest unfrozen successfully"),
            updated,
            _("submission(s) affected"),
        )
        messages.success(request, msg, extra_tags="success")
    else:
        msg = _("Contest couldn't be unfrozen because it is coming or running!")
        messages.warning(request, msg, extra_tags="warning")

    return redirect(reverse("mog:contest_problems", args=(contest.id,)))


@login_required
def contest_stats(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    result = get_contest_stats(contest)

    return JsonResponse(data=result, safe=False)


@login_required
def contest_csv(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    problems, instance_results = calculate_standing(contest, True, None)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="{0}.csv"'.format(
        contest.name.strip()
    )

    response.write("sep=,\r\n")

    writerow = csv.writer(response).writerow

    header = [trans("Rank"), trans("Team"), trans("Solved"), trans("Penalty")]
    header.extend([problem.letter for problem in problems])
    writerow(map(lambda x: x, header))

    append = list.append

    for instance_result in instance_results:
        instance = instance_result.instance
        row = []

        append(row, instance_result.rank)
        name = "" if instance.real else "(~) "

        if instance.team is not None:
            name += instance.team.name.strip()
            if instance.team.institution is not None:
                name += " ({0})".format(instance.team.institution.name.strip())
        else:
            name += instance.user.username.strip()
            if instance.user.profile.institution is not None:
                name += " ({0})".format(instance.user.profile.institution.name.strip())

        append(row, name)
        append(row, instance_result.solved)
        append(row, instance_result.penalty)

        for problem_result in instance_result.problem_results:
            cell = ""
            if problem_result.accepted:
                cell = format_minutes(problem_result.acc_delta)
            if problem_result.attempts > 0:
                cell += " (-{0})".format(problem_result.attempts)
            append(row, cell.strip())
        writerow(row)

    return response


@login_required
def contest_baylor(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    problems, instance_results = calculate_standing(contest, False, None)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="standing_{0}.csv"'.format(
        contest.name
    )

    writer = csv.writer(response)

    header = [
        "teamId",
        "rank",
        "medalCitation",
        "problemsSolved",
        "totalTime",
        "lastProblemTime",
        "siteCitation",
        "citation",
    ]
    writer.writerow(header)

    rank = 0
    last_rank = 0
    for instance_result in instance_results:
        instance = instance_result.instance
        if (
            not instance.team
            or not instance.team.icpcid
            or instance.group == contest.group
        ):
            continue
        if instance.team.icpcid.startswith(ICPCID_GUEST_PREFIX):
            continue

        if instance_result.rank != last_rank:
            rank += 1
        last_rank = instance_result.rank

        row = [
            instance.team.icpcid,
            rank,
            "",
            instance_result.solved,
            instance_result.penalty,
            int(instance_result.last_accepted_delta) // 60,
            instance.group,
            "Rank %d" % rank,
        ]

        writer.writerow(row)

    return response
