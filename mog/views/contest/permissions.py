import csv

from django.urls import reverse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from api.models import (
    Contest,
    User,
    ContestPermission,
)

from mog.forms import (
    ImportPermissionForm,
)
from mog.gating import (
    grant_role_to_user_in_contest,
    revoke_role_to_user_in_contest,
    user_is_admin,
)
from mog.templatetags.security import can_manage_contest

from mog.baylor.utils import CSV_PERMISSION_HEADER

from ..permissions import set_granted_to_permission


@login_required
def contest_permission(request, contest_id):
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
        "mog/contest/permission.html",
        {
            "contest": contest,
            "permissions": ContestPermission.objects.filter(
                contest=contest, role__in=["judge", "observer"]
            ).order_by("-granted", "role", "user__username"),
            "form_import_permission": ImportPermissionForm(),
        },
    )


@login_required
@require_http_methods(["POST"])
def contest_add_permission(request, contest_id):
    """Administrative tool: Add persission to user in contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)

    nxt = request.POST.get("next") or reverse(
        "mog:contest_permission", args=(contest.pk,)
    )
    observer = request.POST.get("observer", "") == "on"
    judge = request.POST.get("judge", "") == "on"
    members = request.POST.get("user-members", "").split(",")
    users = []

    if not observer and not judge:
        msg = _(
            "You need to assign at least one permission (observer / judge) to the users."
        )
        messages.success(request, msg, extra_tags="warning")
        return redirect(nxt)

    if len(members) == 0:
        msg = _("You need to select at least one user.")
        messages.warning(request, msg, extra_tags="warning")
        return redirect(nxt)

    try:
        for member in members:
            users.append(get_object_or_404(User, pk=int(member)))
        users = set(users)

        for user in users:
            if judge:
                prev_permission = ContestPermission.objects.filter(
                    contest=contest, user=user, role="judge"
                ).first()
                if prev_permission:
                    set_granted_to_permission(prev_permission, True)
                else:
                    grant_role_to_user_in_contest(user, contest, role="judge")
            if observer:
                prev_permission = ContestPermission.objects.filter(
                    contest=contest, user=user, role="observer"
                ).first()
                if prev_permission:
                    set_granted_to_permission(prev_permission, True)
                else:
                    grant_role_to_user_in_contest(user, contest, role="observer")
        msg = _("Successfully assigned " + str(len(users)) + " new users roles.")
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(request, "Permission users: Invalid data!", extra_tags="danger")

    return redirect(nxt)


@login_required
def contest_permission_export(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)

    response = HttpResponse(content_type="text/csv")
    response[
        "Content-Disposition"
    ] = 'attachment; filename="permission_{0}.csv"'.format(contest.name)

    writer = csv.writer(response)

    header = ["username", "role", "granted"]
    writer.writerow(header)

    permissions = ContestPermission.objects.filter(
        contest=contest, role__in=["judge", "observer"]
    )

    for permission in permissions:
        row = [permission.user.username, permission.role, permission.granted]
        writer.writerow(row)

    return response


@login_required
@require_http_methods(["POST"])
def contest_permission_import(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not can_manage_contest(request.user, contest):
        return HttpResponseForbidden()

    form_import_permission = ImportPermissionForm(request.POST, request.FILES)

    csv_permission = None

    if form_import_permission.is_valid():
        data = form_import_permission.cleaned_data
        csv_permission = data["csv_permission"]

        if csv_permission:
            try:
                csv_ref = csv_permission.read().decode("utf-8").splitlines()
                reader = list(csv.reader(csv_ref))

                if ",".join(reader[0]).strip() != CSV_PERMISSION_HEADER:
                    raise Exception(
                        "CSV file header must be %s" % CSV_PERMISSION_HEADER
                    )

                for line in reader[1:]:
                    user = User.objects.filter(username=str(line[0])).first()
                    if not user:
                        msg = _("Username " + str(line[0]) + " do not exists.")
                        messages.success(request, msg, extra_tags="warning")
                        continue
                    role = line[1].lower()
                    if role not in ["judge", "observer"]:
                        msg = _("Role " + str(role) + " skiped from import.")
                        messages.warning(request, msg, extra_tags="warning")
                        continue
                    granted = line[2].lower() == "true"
                    prev_permission = ContestPermission.objects.filter(
                        contest=contest, user=user, role=role
                    ).first()
                    if prev_permission:
                        set_granted_to_permission(prev_permission, granted)
                    elif granted:
                        grant_role_to_user_in_contest(user, contest, role)
                    else:
                        revoke_role_to_user_in_contest(user, contest, role)

                msg = _(
                    "Successfully assigned "
                    + str(len(reader) - 1)
                    + " new users roles."
                )
                messages.success(request, msg, extra_tags="success")

            except Exception as e:
                msg = _("Error reading CSV User Permissions file: " + str(e))
                messages.error(request, msg, extra_tags="danger")

    return redirect("mog:contest_permission", contest_id=contest.id)
