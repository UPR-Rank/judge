from django.db.models import signals
from django.dispatch import receiver

from api.lib import cache_keys, constants
from api.models import Contest, ContestPermission, Post, RatingChange, Submission


@receiver(signals.post_delete, sender=Post)
@receiver(signals.post_save, sender=Post)
def clean_ten_most_recent_posts(*args, **kwargs):
    cache_keys.invalidate(constants.CACHE_KEY_TEN_MOST_RECENT_POSTS)


@receiver(signals.post_delete, sender=Contest)
@receiver(signals.post_delete, sender=Submission)
@receiver(signals.post_save, sender=Contest)
@receiver(signals.post_save, sender=Submission)
def clean_five_top_rated_profiles(*args, **kwargs):
    cache_keys.invalidate(constants.CACHE_KEY_FIVE_TOP_RATED_PROFILES)


# The rating filter reads UserProfile.rating, which only moves when a contest
# is rated, and a contest being rated is exactly a RatingChange row being
# written. Invalidating here is what lets the short USER_RATING_TIMEOUT be a
# backstop instead of the thing that makes the page correct.
@receiver(signals.post_delete, sender=RatingChange)
@receiver(signals.post_save, sender=RatingChange)
def clean_user_rating(*args, **kwargs):
    cache_keys.invalidate(constants.CACHE_KEY_USER_RATING)


# Without this a granted or revoked judge/observer role took up to
# USER_CONTESTS_TIMEOUT to apply, because the per-user contest list was only
# dropped by expiry.
@receiver(signals.post_delete, sender=ContestPermission)
@receiver(signals.post_save, sender=ContestPermission)
def clean_user_contests(*args, **kwargs):
    cache_keys.invalidate(constants.CACHE_KEY_USER_CONTESTS)
