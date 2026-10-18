# Cache key names for queries defined in api/lib/queries.py
CACHE_KEY_FIVE_TOP_RATED_PROFILES = "api/lib/five_top_rated_profiles"
CACHE_KEY_TEN_MOST_RECENT_POSTS = "api/lib/ten_most_recent_posts"
CACHE_KEY_USER_CONTESTS = "api/lib/user_contests"
CACHE_KEY_STANDING = "api/lib/standing"

STANDING_TIMEOUT = 10
USER_CONTESTS_TIMEOUT = 300

# Keys owned by the MOG template filters (mog/templatetags/filters.py). They live
# here rather than next to the filters so that invalidation can name them
# without importing the tag library, which would pull Django templates into the
# signal handlers.
CACHE_KEY_DIVISIONS = "mog/filters/divisions"
CACHE_KEY_RESULTS = "mog/filters/results"
CACHE_KEY_USER_RATING = "mog/filters/user_rating"

# These tables change almost exclusively through migrations and administrative
# edits, so the cached copies are given a long lifetime rather than being
# invalidated on every write.
DIVISIONS_TIMEOUT = 24 * 60 * 60
RESULTS_TIMEOUT = 24 * 60 * 60

# A rating only moves when a contest is rated, so the window stays short and the
# key is dropped explicitly when RatingChange rows are written.
USER_RATING_TIMEOUT = 5 * 60

# palantir stores the geo lookup of an IP address under its own key; see
# palantir/utils.py.
CACHE_KEY_IP_PREFIX = "palantir/ip"
IP_INFO_TIMEOUT = 60
