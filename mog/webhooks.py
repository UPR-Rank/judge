from django.conf import settings

from mog.tasks import enqueue, post_to_webhook_task, site_url


def post_content(content):
    for webhook in settings.DISCORD_CLARIFICATION_WEBHOOKS:
        enqueue(post_to_webhook_task, webhook, content)


DESCRIPTION = """[{problem_title}]({problem_url})
{tags}"""


def push_clarification_to_webhooks(clarification, create):
    content = {}

    contest = clarification.contest
    contest_name = contest.name
    content["title"] = contest_name

    base = site_url()
    content["url"] = f"{base}/contest/{contest.pk}/clarifications"

    if clarification.problem:
        problem = clarification.problem
        problem_title = problem.title
        problem_url = f"{base}/problem/{problem.pk}/{problem.slug}/"
    else:
        problem_title = "General"
        problem_url = f"{base}/contest/problems/{contest.pk}"

    tags = []
    if create:
        tags.append("CREATED")
    else:
        tags.append("EDITED")

    if clarification.public:
        tags.append("PUBLIC")
    else:
        tags.append("PRIVATE")

    tags = " ".join(f"`{tag}`" for tag in tags)

    content["description"] = DESCRIPTION.format(
        problem_title=problem_title,
        problem_url=problem_url,
        tags=tags,
    )

    fields = []

    if clarification.answer:
        fields.append(
            {
                "name": "Answer by:",
                "value": clarification.fixer.username,
            }
        )

    fields.append(
        {
            "name": "Question:",
            "value": clarification.question,
        }
    )

    if clarification.answer:
        fields.append(
            {
                "name": "Answer:",
                "value": clarification.answer,
            }
        )

    content["fields"] = fields

    post_content(content)
