# flake8: noqa: F401
"""Contest views, split by responsibility. Re-exports keep `mog.views.contest.<name>` stable."""

from .public import (
    contests,
    contest_clarifications,
    contest_overview,
    contest_problems,
    contest_standing,
    contest_submissions,
    contest_rating_changes,
    team_submissions,
    contest_submissions_export,
)
from .manage import (
    contest_manage,
    contest_manage_import_baylor,
    contest_manage_import_guest,
    contest_manage_export_password,
    remove_contest,
    ContestCreateView,
    ContestEditView,
    CreateProblemInContestView,
)
from .registration import (
    contest_registration,
    register_instance,
    contest_register,
    contest_register_user,
    contest_registration_multiple_register_user,
    contest_register_team,
    contest_registration_multiple_register_team,
    remove_instance,
    contest_remove_instance,
    contest_remove_registration,
    contest_registration_multiple_unregister,
    set_contest_registration_multiple_enable,
    contest_registration_multiple_enable,
    contest_registration_multiple_disable,
    contest_instances_info,
    contest_registration_multiple_edit_group,
)
from .results import (
    contest_saris,
    download_json_saris,
    rate_contest,
    unrate_contest,
    unfreeze_contest,
    contest_stats,
    contest_csv,
    contest_baylor,
)
from .permissions import (
    contest_permission,
    contest_add_permission,
    contest_permission_export,
    contest_permission_import,
)
