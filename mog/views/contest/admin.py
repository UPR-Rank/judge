import zipfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.http import require_http_methods

from api.models import (
    Contest,
    Problem,
    Checker,
)

from mog.forms import (
    ContestForm,
    ProblemInContestForm,
    ImportBaylorForm,
    ImportGuestTeamsForm,
)
from mog.gating import (
    grant_role_to_user_in_contest,
    user_can_create_contest,
    user_is_admin,
)
from mog.templatetags.security import can_manage_contest
from mog.model_helpers.contest import can_create_problem_in_contest
from mog.samples import fix_problem_folder

from mog.baylor.import_baylor import ProcessImportBaylor
from mog.baylor.import_team import ProcessImportTeam
from mog.baylor.team_password import ZipTeamPassword


@login_required
def contest_manage(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not can_manage_contest(request.user, contest):
        return HttpResponseForbidden()

    if user_is_admin(request.user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    return render(
        request,
        "mog/contest/manage.html",
        {
            "contest": contest,
            "form_import_baylor": ImportBaylorForm(),
            "form_import_guest": ImportGuestTeamsForm(),
        },
    )


@login_required
@require_http_methods(["POST"])
def contest_manage_import_baylor(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not can_manage_contest(request.user, contest):
        return HttpResponseForbidden()

    if user_is_admin(request.user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    form_import_baylor = ImportBaylorForm(request.POST, request.FILES)

    zip_baylor = None
    prefix_baylor = None
    select_pending_teams_baylor = False

    if form_import_baylor.is_valid():
        data = form_import_baylor.cleaned_data
        zip_baylor = data["zip_baylor"]
        prefix_baylor = data["prefix_baylor"]
        select_pending_teams_baylor = data["select_pending_teams_baylor"]

        if zip_baylor and prefix_baylor:
            try:
                with zipfile.ZipFile(zip_baylor, "r") as zip_ref:
                    process_baylor_file = ProcessImportBaylor(
                        zip_ref, contest_id, prefix_baylor, select_pending_teams_baylor
                    )
                    results = process_baylor_file.handle()
                    for result in results:
                        if result["type"] == "success":
                            messages.success(
                                request, result["message"], extra_tags="success secure"
                            )
                        else:
                            messages.warning(
                                request, result["message"], extra_tags="warning secure"
                            )
            except Exception as e:
                msg = _("Error reading file from baylor: " + str(e))
                messages.error(request, msg, extra_tags="danger")

    return redirect("mog:contest_manage", contest_id=contest.id)


@login_required
@require_http_methods(["POST"])
def contest_manage_import_guest(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not can_manage_contest(request.user, contest):
        return HttpResponseForbidden()

    form_import_guest = ImportGuestTeamsForm(request.POST, request.FILES)
    csv_teams = None
    prefix_team = None

    if form_import_guest.is_valid():
        data = form_import_guest.cleaned_data
        csv_teams = data["csv_teams"]
        prefix_team = data["prefix_team"]

        if csv_teams and prefix_team:
            try:
                csv_ref = csv_teams.read().decode("utf-8").splitlines()
                process_guest_team = ProcessImportTeam(csv_ref, contest_id, prefix_team)
                results = process_guest_team.handle()
                for result in results:
                    if result["type"] == "success":
                        messages.success(
                            request, result["message"], extra_tags="success secure"
                        )
                    else:
                        messages.warning(
                            request, result["message"], extra_tags="warning secure"
                        )
            except Exception as e:
                msg = _("Error reading CSV Guest Teams file: " + str(e))
                messages.error(request, msg, extra_tags="danger")

    return redirect("mog:contest_manage", contest_id=contest.id)


@login_required
@require_http_methods(["GET"])
def contest_manage_export_password(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not can_manage_contest(request.user, contest):
        return HttpResponseForbidden()

    zip_team_password = ZipTeamPassword(contest)
    zip_passwords = zip_team_password.generate_zip_team_password()
    response = HttpResponse(zip_passwords, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="passwords_{0}.zip"'.format(
        contest.name
    )
    return response


@login_required
@require_http_methods(["POST"])
def remove_contest(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    contest.delete()
    msg = 'Contest "{0}" removed successfully!'.format(contest.name)
    messages.success(request, msg, extra_tags="success")
    return redirect("mog:contests")


class ContestCreateView(View):
    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        if not user_can_create_contest(request.user):
            return HttpResponseForbidden()
        return render(
            request, "mog/contest/create.html", {"form": ContestForm(user=request.user)}
        )

    @method_decorator(login_required)
    def post(self, request, *args, **kwargs):
        if not user_can_create_contest(request.user):
            return HttpResponseForbidden()
        form = ContestForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, "mog/contest/create.html", {"form": form})
        data = form.cleaned_data
        contest = Contest(
            name=data["name"],
            code=data["code"],
            description=data["description"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            visible=data["visible"],
            frozen_time=data["frozen_time"],
            death_time=data["death_time"],
            closed=data["closed"],
            allow_teams=data["allow_teams"],
            student_group=data["student_group"],
            # The ranking is labelled with the class when no group was given.
            group=data["group"]
            or (data["student_group"] and data["student_group"].name),
        )
        contest.save()
        if not user_is_admin(request.user):
            grant_role_to_user_in_contest(request.user, contest, "judge")
        return redirect("mog:contest_problems", contest_id=contest.id)


class ContestEditView(View):
    @method_decorator(login_required)
    def get(self, request, contest_id, *args, **kwargs):
        contest = get_object_or_404(Contest, pk=contest_id)

        if not contest.can_be_edited_by(request.user):
            return HttpResponseForbidden()

        return render(
            request,
            "mog/contest/edit.html",
            {
                "form": ContestForm(instance=contest, user=request.user),
                "contest": contest,
            },
        )

    @method_decorator(login_required)
    def post(self, request, contest_id, *args, **kwargs):
        contest = get_object_or_404(Contest, pk=contest_id)

        if not contest.can_be_edited_by(request.user):
            return HttpResponseForbidden()

        form = ContestForm(request.POST, instance=contest, user=request.user)
        if not form.is_valid():
            return render(
                request,
                "mog/contest/edit.html",
                {
                    "form": form,
                    "contest": contest,
                },
            )
        data = form.cleaned_data
        contest.name = data["name"]
        contest.code = data["code"]
        contest.description = data["description"]
        contest.start_date = data["start_date"]
        contest.end_date = data["end_date"]
        contest.visible = data["visible"]
        contest.frozen_time = data["frozen_time"]
        contest.death_time = data["death_time"]
        contest.closed = data["closed"]
        contest.allow_teams = data["allow_teams"]
        contest.student_group = data["student_group"]
        contest.save()
        return redirect("mog:contest_problems", contest_id=contest.id)


class CreateProblemInContestView(View):
    @method_decorator(login_required)
    def get(self, request, contest_id, *args, **kwargs):
        contest = get_object_or_404(Contest, pk=contest_id)
        if not can_create_problem_in_contest(request.user, contest):
            raise Http404()
        return render(
            request,
            "mog/contest/create_problem.html",
            {"form": ProblemInContestForm(), "contest": contest},
        )

    @method_decorator(login_required)
    def post(self, request, contest_id, *args, **kwargs):
        contest = get_object_or_404(Contest, pk=contest_id)
        if not can_create_problem_in_contest(request.user, contest):
            raise Http404()
        form = ProblemInContestForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "mog/contest/create_problem.html",
                {"form": form, "contest": contest},
            )
        data = form.cleaned_data
        problem = Problem(
            title=data["title"],
            body=data["body"],
            input=data["input"],
            output=data["output"],
            hints=data["hints"],
            time_limit=data["time_limit"],
            memory_limit=data["memory_limit"],
            multiple_limits=data["multiple_limits"],
            checker=Checker.objects.order_by("id").first(),
            position=data["position"],
            balloon=data["balloon"],
            letter_color=data["letter_color"],
            contest=contest,
            is_html=False,
        )
        problem.save()
        problem.tags.set(data["tags"])
        problem.compilers.set(data["compilers"])
        fix_problem_folder(problem)
        return redirect("mog:problem", problem_id=problem.id, slug=problem.slug)
