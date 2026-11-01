"""Teachers, their student groups, and contests open only to a group.

A teacher creates contests for the groups they teach and becomes judge of
them, so everything a contest judge can already do (add problems, see the
frozen standing) comes with it. A contest opened to a group is invisible to
everyone outside it, its problems included.
"""

from datetime import timedelta

from django.utils import timezone

from api.models import Contest, Problem, StudentGroup
from mog.gating import user_is_judge_in_contest
from mog.model_helpers.contest import can_create_problem_in_contest

from . import FixturedTestCase


class StudentGroupTestCase(FixturedTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.updateUserProfile(
            self.newUser("teacher", is_active=True), role="teacher"
        )
        self.student = self.newUser("student", is_active=True)
        self.outsider = self.newUser("outsider", is_active=True)
        self.group = StudentGroup.objects.create(
            name="Algoritmos 1", teacher=self.teacher
        )
        self.group.students.add(self.student)

    def contest_data(self, **kwargs):
        start = timezone.now() + timedelta(hours=1)
        data = {
            "name": "Class contest",
            "code": "class-1",
            "description": "",
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": (start + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "visible": "on",
            "frozen_time": 0,
            "death_time": 0,
            "group": "",
            "student_group": self.group.pk,
        }
        data.update(kwargs)
        return data

    def group_contest(self):
        return self.newContest(
            code="group-1",
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(hours=1),
            student_group=self.group,
        )

    # CP-19: a teacher creates a contest for their group.
    def test_teacher_creates_a_contest_for_their_group_and_judges_it(self):
        self.login(self.teacher)
        response = self.client.post("/contest/create", self.contest_data())

        contest = Contest.objects.get(code="class-1")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(contest.student_group, self.group)
        self.assertEqual(contest.group, "Algoritmos 1")  # ranking label
        self.assertTrue(user_is_judge_in_contest(self.teacher, contest))
        self.assertTrue(can_create_problem_in_contest(self.teacher, contest))
        self.assertTrue(contest.can_be_edited_by(self.teacher))

    def test_teacher_cannot_open_a_contest_to_another_teachers_group(self):
        other = self.updateUserProfile(self.newUser("other"), role="teacher")
        foreign = StudentGroup.objects.create(name="Not mine", teacher=other)
        self.login(self.teacher)

        response = self.client.post(
            "/contest/create", self.contest_data(student_group=foreign.pk)
        )

        self.assertEqual(response.status_code, 200)  # form re-rendered with error
        self.assertFalse(Contest.objects.filter(code="class-1").exists())

    def test_teacher_cannot_edit_a_contest_they_do_not_judge(self):
        self.login(self.teacher)
        self.assertFalse(self.running_contest.can_be_edited_by(self.teacher))
        response = self.client.get("/contest/edit/%d" % self.running_contest.pk)
        self.assertEqual(response.status_code, 403)

    # CP-22: a student cannot create contests.
    def test_student_cannot_create_a_contest(self):
        self.login(self.student)
        self.assertEqual(self.client.get("/contest/create").status_code, 403)
        response = self.client.post("/contest/create", self.contest_data())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Contest.objects.filter(code="class-1").exists())

    # CP-20: a student of another group does not see the contest.
    def test_group_contest_is_visible_only_to_its_group(self):
        contest = self.group_contest()

        self.assertTrue(contest.can_be_seen_by(self.student))
        self.assertTrue(contest.can_be_seen_by(self.teacher))
        self.assertFalse(contest.can_be_seen_by(self.outsider))
        self.assertFalse(contest.overview_can_be_seen_by(self.outsider))

        running, _, _ = Contest.get_all_contests(self.outsider)
        self.assertNotIn(contest, running)
        running, _, _ = Contest.get_all_contests(self.student)
        self.assertIn(contest, running)

        self.login(self.outsider)
        response = self.client.get("/contest/problems/%d" % contest.pk)
        self.assertNotEqual(response.status_code, 200)

    def test_outsider_cannot_register_in_a_group_contest(self):
        contest = self.group_contest()
        self.assertTrue(contest.can_register_for_real(self.student))
        self.assertFalse(contest.can_register_for_real(self.outsider))

    def test_group_contest_problems_stay_out_of_the_public_archive(self):
        contest = self.group_contest()
        problem = self.newProblem("Secret", contest=contest, position=1)

        self.assertNotIn(problem, Problem.get_visible_problems(admin=False))
        self.assertIn(problem, Problem.get_visible_problems(admin=True).all())

    def test_admin_sees_every_group_contest(self):
        contest = self.group_contest()
        admin = self.newAdmin("root")
        self.assertTrue(contest.can_be_seen_by(admin))

    # CP-21: ranking of the users of one institution.
    def test_users_ranking_filters_by_institution(self):
        from api.models import Institution

        upr = Institution.objects.create(name="UPR")
        self.updateUserProfile(self.student, institution=upr)

        response = self.client.get("/users/?institution=%d" % upr.pk)
        usernames = [p.user.username for p in response.context["profiles"]]
        self.assertEqual(usernames, ["student"])
