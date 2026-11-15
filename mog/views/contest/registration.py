from django.db.models.functions import Lower
from django.urls import reverse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.timesince import timesince
from django.views.decorators.http import require_http_methods

from api.models import (
    Contest,
    ContestInstance,
    Team,
    User,
)
from mog.decorators import public_actions_required

from mog.gating import (
    user_is_admin,
)
from mog.templatetags.filters import rating_color, user_color


@login_required
def contest_registration(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)

    if not user_is_admin(request.user):
        raise Http404()

    if user_is_admin(request.user) and contest.needs_unfreeze and contest.is_past:
        msg = _(
            "This contest is still frozen. Go to <b>Actions -> Unfreeze contest </b> to see the final results!"
        )
        messages.warning(request, msg, extra_tags="warning secure")

    return render(
        request,
        "mog/contest/registration.html",
        {
            "contest": contest,
            "instances": contest.instances.order_by(
                Lower("group"), "team__institution__name", "team__name"
            ),
        },
    )


@login_required
def register_instance(request, contest, user, team):
    """User in request is registering user/team"""

    nxt = request.POST.get("next")

    if not user_is_admin(request.user):
        if not contest.visible:
            # Admins are the only ones that can register user when
            # contest is hidden.
            raise Http404()

        if contest.closed and not contest.is_past:
            # Admins are the only ones that can register user when
            # registration is closed.
            msg = _('Registration is closed for contest "{0}"'.format(contest.name))
            messages.warning(request, msg, extra_tags="danger")
            return redirect(nxt or reverse("mog:contests"))

    # Check that no user has registered before.
    if team:
        users = [profile.user for profile in team.profiles.all()]
    elif user:
        users = [user]
    else:
        messages.error(request, _("No user to register"), extra_tags="error")
        return redirect(nxt or reverse("mog:contests"))

    # Check that user belongs to team
    if user and user not in users:
        messages.warning(
            request, _("Invalid user/team combination"), extra_tags="warning"
        )
        return redirect(nxt or reverse("mog:contests"))

    # Check that the team can be registered in the contest
    if team and not contest.allow_teams:
        messages.warning(
            request, _("The contest doesn't allow teams"), extra_tags="warning"
        )
        return redirect(nxt or reverse("mog:contests"))

    bypass_closed = user_is_admin(request.user)
    # Check that every user can register for real
    if all(contest.can_register_for_real(u, bypass_closed) for u in users):
        real, start_date = True, None
    elif all(contest.can_register_for_virtual(u) for u in users):
        real, start_date = False, timezone.now()
    else:
        msg = _(
            "Registration cannot be accomplished, some of the team members cannot participate"
        )
        messages.error(request, msg, extra_tags="danger")
        return redirect(nxt or reverse("mog:contests"))

    ContestInstance.objects.create(
        contest=contest,
        user=user,
        team=team,
        real=real,
        start_date=start_date,
        group=contest.group,
    )

    if real:
        msg = _("Successfully registered for real participation")
    else:
        msg = _("Successfully registered for virtual participation")

    messages.success(request, msg, extra_tags="success")

    if real:
        return redirect(nxt or reverse("mog:contests"))

    return redirect(nxt or reverse("mog:contest_problems", args=(contest.pk,)))


@public_actions_required
@login_required
@require_http_methods(["POST"])
def contest_register(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    user = request.user
    try:
        team_id = int(request.POST.get("team"))
        team = get_object_or_404(Team, pk=team_id)
    except (ValueError, TypeError) as _:  # noqa: F841
        team = None
    return register_instance(request, contest, user, team)


@login_required
@require_http_methods(["POST"])
def contest_register_user(request, contest_id):
    """Administrative tool: Register user in contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    try:
        user_id = int(request.POST.get("user"))
    except (TypeError, ValueError):
        raise Http404()
    contest = get_object_or_404(Contest, pk=contest_id)
    user = get_object_or_404(User, pk=user_id)
    return register_instance(request, contest, user, None)


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_register_user(request, contest_id):
    """Administrative tool: Register users in contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    group = request.POST.get("user-group", "").strip()
    members = request.POST.get("user-members", "").split(",")
    users = []

    if group in ["<all>", "<one>"]:
        messages.error(request, 'Invalid group name "%s"' % group, extra_tags="danger")
        return redirect(reverse("mog:contest_registration", args=(contest.pk,)))

    try:
        for member in members:
            users.append(get_object_or_404(User, pk=int(member)))
        users = set(users)
        count = 0
        for user in users:
            if ContestInstance.objects.filter(contest=contest, user=user).first():
                messages.warning(
                    request,
                    _("User '%s' is already registerd" % user.username),
                    extra_tags="warning",
                )
                continue

            if ContestInstance.objects.filter(
                Q(contest_id=contest.id), Q(team__profiles__user_id=user.id)
            ):
                messages.warning(
                    request,
                    _('The user "%s" is already registered as a team!' % user.username),
                    extra_tags="warning",
                )
                continue

            ContestInstance.objects.create(
                contest=contest, user=user, real=True, group=group or contest.group
            )
            count += 1
        msg = _("Successfully registered " + str(count) + " new user")
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(request, "Register users: Invalid data!", extra_tags="danger")

    return redirect(reverse("mog:contest_registration", args=(contest.pk,)))


@login_required
@require_http_methods(["POST"])
def contest_register_team(request, contest_id):
    """Administrative tool: Register team in contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    try:
        team_id = int(request.POST.get("team"))
    except (TypeError, ValueError):
        raise Http404()
    contest = get_object_or_404(Contest, pk=contest_id)
    team = get_object_or_404(Team, pk=team_id)
    return register_instance(request, contest, None, team)


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_register_team(request, contest_id):
    """Administrative tool: Register teams in contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    group = request.POST.get("team-group", "").strip()
    members = request.POST.get("team-members", "").split(",")
    teams = []

    if group in ["<all>", "<one>"]:
        messages.error(request, 'Invalid group name "%s"' % group, extra_tags="danger")
        return redirect(reverse("mog:contest_registration", args=(contest.pk,)))

    try:
        for member in members:
            teams.append(get_object_or_404(Team, pk=int(member)))
        teams = set(teams)
        count = 0
        for team in teams:
            # Check that the team can be registered in the contest
            if not contest.allow_teams:
                messages.warning(
                    request, _("The contest doesn't allow teams"), extra_tags="warning"
                )
                return redirect(reverse("mog:contests"))

            if ContestInstance.objects.filter(
                contest=contest, team__name=team.name
            ).exists():
                messages.warning(
                    request,
                    _(
                        'There is a team registered with the same name "%s", please check it out, register skiped!'
                        % team.name
                    ),
                    extra_tags="warning",
                )
                continue

            if ContestInstance.objects.filter(contest=contest, team=team).first():
                messages.warning(
                    request,
                    _("Team '%s' is already registerd" % team.name),
                    extra_tags="warning",
                )
                continue

            # team > profiles > [user_id]
            profiles = [profile.user.id for profile in team.profiles.all()]
            if ContestInstance.objects.filter(
                Q(contest_id=contest.id),
                Q(user_id__in=profiles) | Q(team__profiles__user_id__in=profiles),
            ):
                messages.warning(
                    request,
                    _(
                        'There are an user within the team "%s" that is already registered with another team or as an user, please check it out, register skiped!'
                        % team.name
                    ),
                    extra_tags="warning",
                )
                continue

            ContestInstance.objects.create(
                contest=contest, team=team, real=True, group=group or contest.group
            )
            count += 1
        msg = _("Successfully registered " + str(count) + " new team")
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(request, "Register teams: Invalid data!", extra_tags="danger")

    return redirect(reverse("mog:contest_registration", args=(contest.pk,)))


def remove_instance(request, instance):
    nxt = request.POST.get("next")

    if not instance:
        msg = _("Instance does not exist")
        messages.info(request, msg, extra_tags="info")
        return redirect(nxt or reverse("mog:contests"))

    if instance.submissions.count() > 0:
        msg = _(
            "Cannot remove registration because the user/team has actions on the contest."
        )
        messages.warning(request, msg, extra_tags="warning")
        return redirect(nxt or reverse("mog:contests"))

    user = request.user

    if instance.contest.closed and not user_is_admin(user):
        msg = _("Cannot remove registration because the contest is closed.")
        messages.warning(request, msg, extra_tags="warning")
        return redirect(nxt or reverse("mog:contests"))

    if not user_is_admin(user):
        if instance.user != user and (
            instance.team and user.profile not in instance.team.profiles.all()
        ):
            return HttpResponseForbidden()

    instance.delete()

    msg = _("Successfully unregistered!")
    messages.success(request, msg, extra_tags="success")

    return redirect(nxt or reverse("mog:contests"))


@login_required
@require_http_methods(["POST"])
def contest_remove_instance(request, instance_id):
    """
    This action is more flexible than `contest_remove_registration`
    and will be effectively used by admins, however, common users
    can post to this URL too removing only their own instances.
    """
    instance = ContestInstance.objects.filter(id=instance_id).first()
    return remove_instance(request, instance)


@public_actions_required
@login_required
@require_http_methods(["POST"])
def contest_remove_registration(request, contest_id):
    """
    Used by common users to remove their participation from a given
    contest without specify the target instance.
    """
    contest = get_object_or_404(Contest, pk=contest_id)
    instance = contest.registration(request.user)
    return remove_instance(request, instance)


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_unregister(request, contest_id):
    """Administrative tool: Remove registration for multiple user/team"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    nxt = request.POST.get("next") or reverse(
        "mog:contest_registration", args=(contest.pk,)
    )
    instances_selected = request.POST.get("instances-unregister-selected", "").split(
        ","
    )
    instances = []

    try:
        for selected in instances_selected:
            instances.append(get_object_or_404(ContestInstance, pk=int(selected)))
        instances = set(instances)
        count = 0
        for instance in instances:
            if not instance:
                continue
            if instance.team:
                team = True
                name = instance.team.name
            else:
                team = False
                name = instance.user.username
            if instance.submissions.count() > 0:
                msg = _(
                    "Cannot remove registration of '%s' because the %s has actions on the contest."
                    % (name, "team" if team else "user")
                )
                messages.warning(request, msg, extra_tags="warning")
                continue
            instance.delete()
            count += 1
        msg = _("Successfully unregistered %d user/team!" % count)
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(
            request, "Unregister user/team: Invalid data!", extra_tags="danger"
        )

    return redirect(nxt)


def set_contest_registration_multiple_enable(
    request, contest_id, instances_selected, is_active
):
    """Administrative tool: Enable multiple instance users from contest"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    contest = get_object_or_404(Contest, pk=contest_id)
    nxt = request.POST.get("next") or reverse(
        "mog:contest_registration", args=(contest.pk,)
    )
    instances = []

    try:
        count = 0
        for selected in instances_selected:
            instances.append(get_object_or_404(ContestInstance, pk=int(selected)))
        instances = set(instances)
        for instance in instances:
            if instance.is_active != is_active:
                instance.is_active = is_active
                instance.save()
                count += 1

        msg = _(
            "Successfully %s %d users" % ("enable" if is_active else "disable", count)
        )
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(
            request, "Enable/disable users: Invalid data!", extra_tags="danger"
        )

    return redirect(nxt)


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_enable(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    instances_selected = request.POST.get("instances-enable-selected", "").split(",")
    return set_contest_registration_multiple_enable(
        request, contest_id, instances_selected, True
    )


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_disable(request, contest_id):
    if not user_is_admin(request.user):
        return HttpResponseForbidden()
    instances_selected = request.POST.get("instances-disable-selected", "").split(",")
    return set_contest_registration_multiple_enable(
        request, contest_id, instances_selected, False
    )


@login_required
@require_http_methods(["GET"])
def contest_instances_info(request, contest_id):
    if not user_is_admin(request.user):
        raise Http404()
    contest = get_object_or_404(Contest, pk=contest_id)
    instances = contest.instances.order_by(Lower("group"))
    instances_data = {}
    for instance in instances:
        if instance.team:
            team = instance.team
            last_login = None
            list_profiles = []
            for profile in team.profiles.all():
                list_profiles.append(
                    {
                        "id": profile.user.id,
                        "username": profile.user.username,
                        "rating_color": rating_color(profile.rating),
                    }
                )
                if last_login is None:
                    last_login = profile.user.last_login
                else:
                    last_login = max(last_login, profile.user.last_login)
            instances_data[instance.pk] = {
                "team": {
                    "name": team.name,
                    "last_login": timesince(last_login) if last_login else None,
                    "profiles": list_profiles,
                },
            }
        elif instance.user:
            user = instance.user
            instances_data[instance.pk] = {
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "last_login": (
                        timesince(user.last_login) if user.last_login else None
                    ),
                    "rating_color": user_color(user),
                },
            }
    return JsonResponse(data={"success": True, "data": instances_data})


@login_required
@require_http_methods(["POST"])
def contest_registration_multiple_edit_group(request, contest_id):
    """Administrative tool: Edit group for multiple user/team"""
    if not user_is_admin(request.user):
        return HttpResponseForbidden()

    contest = get_object_or_404(Contest, pk=contest_id)
    nxt = request.POST.get("next") or reverse(
        "mog:contest_registration", args=(contest.pk,)
    )
    group = request.POST.get("instances-group", "").strip()
    instances_selected = request.POST.get("instances-group-selected", "").split(",")
    instances = []

    if group in ["<all>", "<one>"]:
        messages.error(request, 'Invalid group name "%s"' % group, extra_tags="danger")
        return redirect(nxt)

    try:
        for selected in instances_selected:
            instances.append(get_object_or_404(ContestInstance, pk=int(selected)))
        instances = set(instances)
        count = 0
        for instance in instances:
            if not instance:
                continue
            instance.group = group or contest.group
            instance.save()
            count += 1
        msg = _("Successfully moved %d user/team to Group '%s'" % (count, group))
        messages.success(request, msg, extra_tags="success")

    except (ValueError, TypeError):
        messages.error(
            request, "Unregister user/team: Invalid data!", extra_tags="danger"
        )

    return redirect(nxt)
